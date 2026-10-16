from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import threading
import tomllib
import webbrowser
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import httpx
from filelock import FileLock

from app.config import REPOSITORY_ROOT, Settings, _opencode_config_path, default_settings

START_TIMEOUT_SECONDS = 10.0
STOP_TIMEOUT_SECONDS = 5.0
LOG_NAME = "jobtron.log"


class PortConflictError(RuntimeError):
    pass


class StartFailedError(RuntimeError):
    def __init__(self, message: str, log_path: Path, log_tail: list[str]):
        super().__init__(message)
        self.log_path = log_path
        self.log_tail = log_tail


def log_path(settings: Settings) -> Path:
    return settings.data_dir / LOG_NAME


def data_store_id(settings: Settings) -> str:
    return sha256(str(settings.database_path.resolve()).encode()).hexdigest()[:12]


def _port_conflict_message(settings: Settings) -> str:
    return (
        f"Port {settings.port} on {settings.host} is already used by something that is not "
        "this Jobtron data store. Free the port, or set JOBTRON_PORT to choose another one."
    )


def probe(settings: Settings) -> str:
    """Classify what answers at the configured address: 'ours', 'foreign' or 'free'."""
    try:
        response = httpx.get(f"{settings.frontend_base_url}/healthz", timeout=1.0)
    except httpx.ConnectError:
        return "free"
    except httpx.HTTPError:
        return "foreign"
    try:
        body = response.json()
    except ValueError:
        return "foreign"
    if (
        isinstance(body, dict)
        and body.get("service") == "jobtron6900"
        and body.get("data_store") == data_store_id(settings)
    ):
        return "ours"
    return "foreign"


def read_owner(settings: Settings) -> dict:
    try:
        value = json.loads(settings.owner_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _log_tail(path: Path, lines: int = 8) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def _result(settings: Settings, pid: int | None, already_running: bool) -> dict:
    return {
        "url": settings.frontend_base_url,
        "applications_url": f"{settings.frontend_base_url}/applications",
        "pid": pid,
        "already_running": already_running,
        "log_path": str(log_path(settings)),
    }


def ensure_running(settings: Settings) -> dict:
    """Make sure a detached Jobtron web UI serves this data store; never writes to stdout."""
    state = probe(settings)
    if state == "ours":
        return _result(settings, read_owner(settings).get("pid"), True)
    if state == "foreign":
        raise PortConflictError(_port_conflict_message(settings))

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # Several MCP sessions often start at once; only one may spawn the UI.
    with FileLock(str(settings.data_dir / ".jobtron-start.lock"), timeout=START_TIMEOUT_SECONDS + 5):
        state = probe(settings)
        if state == "ours":
            return _result(settings, read_owner(settings).get("pid"), True)
        if state == "foreign":
            raise PortConflictError(_port_conflict_message(settings))
        return _spawn_and_wait(settings)


def _spawn_and_wait(settings: Settings) -> dict:
    path = log_path(settings)
    environment = dict(os.environ)
    environment.update(
        {
            "JOBTRON_DATA_DIR": str(settings.data_dir),
            "JOBTRON_HOST": settings.host,
            "JOBTRON_PORT": str(settings.port),
            "JOBTRON_CLIENT": settings.client_name,
        }
    )
    with path.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.cli", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            start_new_session=True,
            env=environment,
            cwd=REPOSITORY_ROOT,
        )
    threading.Thread(target=process.wait, name="jobtron-ui-reaper", daemon=True).start()

    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if probe(settings) == "ours":
            return _result(settings, process.pid, False)
        code = process.poll()
        if code is not None:
            raise StartFailedError(
                f"Jobtron exited during startup (exit code {code}).", path, _log_tail(path)
            )
        time.sleep(0.2)
    raise StartFailedError(
        f"Jobtron did not answer at {settings.frontend_base_url} within "
        f"{START_TIMEOUT_SECONDS:g} seconds.",
        path,
        _log_tail(path),
    )


def sessions_dir(settings: Settings) -> Path:
    return settings.data_dir / "sessions"


def register_session(settings: Settings) -> Path:
    """Record this MCP process so `jobtron status` can count live sessions."""
    directory = sessions_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{os.getpid()}.json"
    temporary = directory / f".{os.getpid()}.json.tmp"
    temporary.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "client": settings.client_name,
                "started_at": datetime.now(UTC).isoformat(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _process_is_mcp_server(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    return "jobtron-mcp" in raw.decode(errors="replace")


def live_sessions(settings: Settings) -> list[dict]:
    """Session records whose process is still a running jobtron-mcp; stale records are deleted."""
    live: list[dict] = []
    for path in sorted(sessions_dir(settings).glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid = value.get("pid") if isinstance(value, dict) else None
        if _pid_alive(pid) and _process_is_mcp_server(pid):
            live.append(value)
        else:
            path.unlink(missing_ok=True)
    return live


def _process_is_cli_server(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    parts = raw.decode(errors="replace").split("\0")
    return "app.cli" in parts and "serve" in parts


def stop_running(settings: Settings) -> tuple[bool, str]:
    state = probe(settings)
    if state == "free":
        return True, "Jobtron web UI is not running."
    if state == "foreign":
        return False, _port_conflict_message(settings)
    pid = read_owner(settings).get("pid")
    if not _pid_alive(pid):
        return False, "Jobtron answers, but its owner record has no live process; not stopping anything."
    if not _process_is_cli_server(pid):
        return False, (
            f"The Jobtron on this port (pid {pid}) was not started by 'jobtron start'; "
            "close the client that owns it instead."
        )
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if probe(settings) != "ours" and not _pid_alive(pid):
            return True, f"Stopped Jobtron web UI (pid {pid})."
        time.sleep(0.2)
    return False, f"Sent SIGTERM to pid {pid}, but it is still answering after {STOP_TIMEOUT_SECONDS:g} seconds."


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def agent_registration() -> dict[str, str]:
    result: dict[str, str] = {}

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    codex_config = codex_home / "config.toml"
    if not codex_config.exists():
        result["codex"] = "no"
    else:
        try:
            value = tomllib.loads(codex_config.read_text(encoding="utf-8"))
            servers = value.get("mcp_servers", {})
            result["codex"] = "yes" if isinstance(servers, dict) and "jobtron6900" in servers else "no"
        except (OSError, ValueError):
            result["codex"] = "unknown"

    claude_root = os.environ.get("CLAUDE_CONFIG_DIR")
    claude_config = (
        Path(claude_root).expanduser() / ".claude.json" if claude_root else Path.home() / ".claude.json"
    )
    if not claude_config.exists():
        result["claude"] = "no"
    else:
        value = _read_json(claude_config)
        if value is None:
            result["claude"] = "unknown"
        else:
            servers = value.get("mcpServers", {})
            projects = value.get("projects", {})
            if isinstance(servers, dict) and "jobtron6900" in servers:
                result["claude"] = "yes"
            elif isinstance(projects, dict) and any(
                isinstance(project, dict)
                and isinstance(project.get("mcpServers"), dict)
                and "jobtron6900" in project["mcpServers"]
                for project in projects.values()
            ):
                result["claude"] = "yes (project)"
            else:
                result["claude"] = "no"

    opencode_config = _opencode_config_path()
    if not opencode_config.exists():
        result["opencode"] = "no"
    else:
        value = _read_json(opencode_config)
        if value is None:
            result["opencode"] = "unknown"
        else:
            servers = value.get("mcp", {})
            result["opencode"] = "yes" if isinstance(servers, dict) and "jobtron6900" in servers else "no"
    return result


def _format_started(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return "unknown time"


def _registration_line() -> str:
    registration = agent_registration()
    return "Agent registration: " + " · ".join(
        f"{name} {state}" for name, state in registration.items()
    )


def _sessions_line(settings: Settings) -> str:
    sessions = live_sessions(settings)
    if not sessions:
        return "MCP sessions: none online"
    counts: dict[str, int] = {}
    for session in sessions:
        client = str(session.get("client", "unknown"))
        counts[client] = counts.get(client, 0) + 1
    breakdown = ", ".join(
        f"{client} ×{count}" for client, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )
    return f"MCP sessions: {len(sessions)} online ({breakdown})"


def _command_serve(_: argparse.Namespace) -> int:
    from app.main import run

    run()
    return 0


def _command_start(_: argparse.Namespace) -> int:
    settings = default_settings()
    try:
        info = ensure_running(settings)
    except PortConflictError as exc:
        print(f"jobtron: {exc}", file=sys.stderr)
        return 1
    except StartFailedError as exc:
        print(f"jobtron: {exc}", file=sys.stderr)
        print(f"Log: {exc.log_path}", file=sys.stderr)
        for line in exc.log_tail:
            print(f"  {line}", file=sys.stderr)
        return 1
    if info["already_running"]:
        print(f"Jobtron already running at {info['applications_url']}")
    else:
        print(f"Jobtron started at {info['applications_url']} (pid {info['pid']})")
    return 0


def _command_open(args: argparse.Namespace) -> int:
    code = _command_start(args)
    if code != 0:
        return code
    url = f"{default_settings().frontend_base_url}/applications"
    try:
        webbrowser.open(url)
    except webbrowser.Error:
        pass
    return 0


def _command_stop(_: argparse.Namespace) -> int:
    ok, message = stop_running(default_settings())
    print(message)
    return 0 if ok else 1


def _command_status(_: argparse.Namespace) -> int:
    settings = default_settings()
    state = probe(settings)
    if state == "ours":
        owner = read_owner(settings)
        print(f"Jobtron web UI: running  {settings.frontend_base_url}/applications")
        print(
            f"  pid {owner.get('pid', 'unknown')} · started {_format_started(owner.get('started_at'))}"
            f" · by {owner.get('client', 'unknown')}"
        )
        print(f"  data: {settings.data_dir}   log: {log_path(settings)}")
        print(_sessions_line(settings))
        print(_registration_line())
        return 0
    if state == "foreign":
        print(f"Jobtron web UI: port conflict  {_port_conflict_message(settings)}")
    else:
        print("Jobtron web UI: not running  (start it with: uv run jobtron open)")
    print(_sessions_line(settings))
    print(_registration_line())
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobtron", description="Run and inspect the Jobtron web UI as a background process."
    )
    subparsers = parser.add_subparsers(dest="action")
    for name, handler, help_text in (
        ("serve", _command_serve, "Run the web UI in the foreground"),
        ("start", _command_start, "Start the web UI in the background"),
        ("open", _command_open, "Start the web UI if needed, then open it in the browser"),
        ("status", _command_status, "Report whether the web UI is running"),
        ("stop", _command_stop, "Stop the background web UI"),
    ):
        subparsers.add_parser(name, help=help_text).set_defaults(handler=handler)

    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
