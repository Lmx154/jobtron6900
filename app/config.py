import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout
from platformdirs import user_data_path


APP_NAME = "jobtron6900"
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
LEGACY_DATA_DIR = REPOSITORY_ROOT / "data"


def canonical_data_dir() -> Path:
    return Path(user_data_path(APP_NAME, appauthor=False))


def resolved_data_dir() -> Path:
    configured = os.environ.get("JOBTRON_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    canonical = canonical_data_dir()
    if (canonical / "app.db").exists():
        return canonical
    if (LEGACY_DATA_DIR / "app.db").exists():
        return LEGACY_DATA_DIR
    return canonical


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    client_name: str = "standalone"

    @property
    def frontend_base_url(self) -> str:
        browser_host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        if ":" in browser_host and not browser_host.startswith("["):
            browser_host = f"[{browser_host}]"
        return f"http://{browser_host}:{self.port}"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / ".jobtron.lock"

    @property
    def owner_path(self) -> Path:
        return self.data_dir / ".jobtron-owner.json"


def default_settings() -> Settings:
    return Settings(
        resolved_data_dir(),
        host=os.environ.get("JOBTRON_HOST", "127.0.0.1"),
        port=int(os.environ.get("JOBTRON_PORT", "8765")),
        client_name=os.environ.get("JOBTRON_CLIENT", "standalone"),
    )


class InstanceInUseError(RuntimeError):
    pass


class InstanceLease(AbstractContextManager["InstanceLease"]):
    """Own the one Jobtron MCP/server process allowed to use a data store."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.token = uuid4().hex
        self._lock = FileLock(str(settings.lock_path))
        self._held = False

    def __enter__(self) -> "InstanceLease":
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire(timeout=0)
        except Timeout as exc:
            owner = self._read_owner()
            client = owner.get("client", "another client")
            pid = owner.get("pid", "unknown")
            started = owner.get("started_at", "unknown time")
            raise InstanceInUseError(
                f"Jobtron is already in use by {client} (PID {pid}, started {started}). "
                "Close that client or session before starting Jobtron again."
            ) from exc
        self._held = True
        self.settings.owner_path.write_text(
            json.dumps(
                {
                    "token": self.token,
                    "pid": os.getpid(),
                    "client": self.settings.client_name,
                    "started_at": datetime.now(UTC).isoformat(),
                    "frontend_url": self.settings.frontend_base_url,
                    "database_path": str(self.settings.database_path.resolve()),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._held:
            owner = self._read_owner()
            if owner.get("token") == self.token:
                self.settings.owner_path.unlink(missing_ok=True)
            self._lock.release()
            self._held = False

    def _read_owner(self) -> dict:
        try:
            value = json.loads(self.settings.owner_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}


def _sqlite_integrity(database_path: Path) -> str:
    if not database_path.exists():
        return "missing"
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "unknown"


def migrate_legacy_data(
    source: Path = LEGACY_DATA_DIR,
    destination: Path | None = None,
) -> Path:
    destination = (destination or canonical_data_dir()).expanduser().resolve()
    source = source.expanduser().resolve()
    source_database = source / "app.db"
    destination_database = destination / "app.db"
    if not source_database.exists():
        raise RuntimeError(f"No legacy Jobtron database was found at {source_database}")
    if destination_database.exists():
        if source_database.samefile(destination_database):
            return destination
        raise RuntimeError(
            f"Refusing to overwrite the existing Jobtron database at {destination_database}. "
            f"The legacy database remains at {source_database}."
        )
    if _sqlite_integrity(source_database) != "ok":
        raise RuntimeError(f"Legacy database integrity check failed: {source_database}")

    migration_lock = FileLock(str(source / ".jobtron.lock"))
    try:
        migration_lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError(
            "Jobtron is currently using the legacy data directory. "
            "Close the active client before migrating."
        ) from exc

    stage_root: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and any(destination.iterdir()):
            raise RuntimeError(
                f"Refusing to migrate into non-empty directory {destination}. "
                "Choose an empty JOBTRON_DATA_DIR or reconcile it manually."
            )
        stage_root = Path(
            tempfile.mkdtemp(prefix=".jobtron-migrate-", dir=destination.parent)
        )
        stage = stage_root / "data"
        stage.mkdir()
        for item in source.iterdir():
            if item.name.startswith("app.db") or item.name.startswith(".jobtron"):
                continue
            target = stage / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

        with sqlite3.connect(source_database, timeout=10) as source_connection:
            with sqlite3.connect(stage / "app.db") as destination_connection:
                source_connection.backup(destination_connection)

        if _sqlite_integrity(stage / "app.db") != "ok":
            raise RuntimeError("Copied Jobtron database failed its integrity check")
        if destination.exists():
            destination.rmdir()
        os.replace(stage, destination)
    finally:
        if stage_root is not None:
            shutil.rmtree(stage_root, ignore_errors=True)
        migration_lock.release()
    return destination


def _uv_server_command() -> list[str]:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv was not found on PATH. Install uv, then rerun jobtron-setup.")
    return [str(Path(uv).resolve()), "run", "--directory", str(REPOSITORY_ROOT), "jobtron-mcp"]


def _client_environment(data_dir: Path, client: str) -> dict[str, str]:
    return {
        "JOBTRON_DATA_DIR": str(data_dir.resolve()),
        "JOBTRON_CLIENT": client,
    }


def _opencode_config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    return root / "opencode" / "opencode.json"


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{path} is JSONC or invalid JSON. Jobtron will not rewrite it and risk losing comments. "
            "Use 'jobtron-setup print-config --client opencode' and merge the entry manually."
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object in {path}")
    return value


def _write_json_object(path: Path, value: dict, dry_run: bool = False) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".jobtron-backup"))
    temporary = path.with_suffix(path.suffix + ".jobtron-tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def opencode_entry(data_dir: Path) -> dict:
    return {
        "type": "local",
        "command": _uv_server_command(),
        "enabled": True,
        "environment": _client_environment(data_dir, "opencode"),
        "timeout": 20000,
    }


def _run_client_command(command: list[str], dry_run: bool = False, check: bool = True) -> None:
    if dry_run:
        print(" ".join(command))
        return
    if not shutil.which(command[0]) and not Path(command[0]).exists():
        raise RuntimeError(f"Required client command was not found: {command[0]}")
    subprocess.run(command, check=check)


def _install_codex(data_dir: Path, dry_run: bool) -> None:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("Codex CLI was not found on PATH")
    _run_client_command([codex, "mcp", "remove", "jobtron6900"], dry_run, check=False)
    environment = _client_environment(data_dir, "codex")
    server_command = _uv_server_command()
    command = [codex, "mcp", "add", "jobtron6900"]
    for key, value in environment.items():
        command.extend(["--env", f"{key}={value}"])
    command.extend(["--", *server_command])
    _run_client_command(command, dry_run)


def _install_claude(data_dir: Path, dry_run: bool) -> None:
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("Claude Code was not found on PATH")
    _run_client_command(
        [claude, "mcp", "remove", "--scope", "user", "jobtron6900"],
        dry_run,
        check=False,
    )
    environment = _client_environment(data_dir, "claude")
    server_command = _uv_server_command()
    command = [claude, "mcp", "add", "--scope", "user"]
    for key, value in environment.items():
        command.extend(["--env", f"{key}={value}"])
    command.extend(["--transport", "stdio", "jobtron6900", "--", *server_command])
    _run_client_command(command, dry_run)


def _install_opencode(data_dir: Path, dry_run: bool) -> None:
    path = _opencode_config_path()
    value = _read_json_object(path)
    servers = value.setdefault("mcp", {})
    if not isinstance(servers, dict):
        raise RuntimeError(f"Expected 'mcp' to be an object in {path}")
    servers["jobtron6900"] = opencode_entry(data_dir)
    value.setdefault("$schema", "https://opencode.ai/config.json")
    _write_json_object(path, value, dry_run)
    if dry_run:
        print(json.dumps({"path": str(path), "entry": servers["jobtron6900"]}, indent=2))


def install_clients(clients: list[str], data_dir: Path, dry_run: bool = False) -> None:
    _uv_server_command()
    missing = [
        client
        for client in clients
        if client in {"codex", "claude"} and not shutil.which(client)
    ]
    if missing:
        raise RuntimeError(f"Required client command(s) not found: {', '.join(missing)}")
    if "opencode" in clients:
        _read_json_object(_opencode_config_path())

    installers = {
        "codex": _install_codex,
        "opencode": _install_opencode,
        "claude": _install_claude,
    }
    for client in clients:
        installers[client](data_dir, dry_run)


def _remove_opencode(dry_run: bool) -> None:
    path = _opencode_config_path()
    value = _read_json_object(path)
    servers = value.get("mcp")
    if isinstance(servers, dict):
        servers.pop("jobtron6900", None)
    _write_json_object(path, value, dry_run)


def uninstall_clients(clients: list[str], dry_run: bool = False) -> None:
    for client in clients:
        if client == "codex":
            executable = shutil.which("codex")
            if executable:
                _run_client_command(
                    [executable, "mcp", "remove", "jobtron6900"], dry_run, check=False
                )
        elif client == "claude":
            executable = shutil.which("claude")
            if executable:
                _run_client_command(
                    [executable, "mcp", "remove", "--scope", "user", "jobtron6900"],
                    dry_run,
                    check=False,
                )
        else:
            _remove_opencode(dry_run)


def _manifest_summary(settings: Settings) -> tuple[int, str, int]:
    from app.db import Database
    from app.mcp.server import build_server
    from app.services import Services
    from app.storage import FileStorage

    database = Database(settings.database_path)
    server = build_server(Services(database, FileStorage(settings.files_dir)), settings.frontend_base_url)
    tools = asyncio.run(server.list_tools())
    manifest = [
        {
            "name": tool.name,
            "inputSchema": tool.input_schema,
        }
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    instructions = server._lowlevel_server.create_initialization_options().instructions
    return len(tools), digest, len(instructions or "")


def doctor(data_dir: Path) -> bool:
    settings = Settings(data_dir)
    integrity = _sqlite_integrity(settings.database_path)
    tool_count, manifest_hash, instruction_chars = _manifest_summary(settings)
    healthy = integrity == "ok" and instruction_chars <= 2000
    print(f"data_dir: {settings.data_dir}")
    print(f"database_integrity: {integrity}")
    print(f"tool_count: {tool_count}")
    print(f"tool_manifest: {manifest_hash}")
    print(f"server_instruction_characters: {instruction_chars}")
    print(f"frontend_url: {settings.frontend_base_url}/applications")
    print(f"single_client_lock: {settings.lock_path}")
    return healthy


def _parse_clients(value: str) -> list[str]:
    clients = [item.strip().lower() for item in value.split(",") if item.strip()]
    invalid = sorted(set(clients) - {"codex", "opencode", "claude"})
    if invalid:
        raise argparse.ArgumentTypeError(f"Unsupported client(s): {', '.join(invalid)}")
    return clients


def setup_main() -> int:
    parser = argparse.ArgumentParser(
        prog="jobtron-setup",
        description="Install and diagnose Jobtron for Codex, OpenCode, and Claude Code.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    install = subparsers.add_parser("install", help="Migrate data and install user-level MCP entries")
    install.add_argument("--clients", type=_parse_clients, default=["codex", "opencode", "claude"])
    install.add_argument("--data-dir", type=Path, default=canonical_data_dir())
    install.add_argument("--dry-run", action="store_true")

    migrate = subparsers.add_parser("migrate", help="Copy legacy repository data safely")
    migrate.add_argument("--source", type=Path, default=LEGACY_DATA_DIR)
    migrate.add_argument("--data-dir", type=Path, default=canonical_data_dir())

    check = subparsers.add_parser("doctor", help="Check shared data and the MCP contract")
    check.add_argument("--data-dir", type=Path, default=resolved_data_dir())

    status = subparsers.add_parser("status", help="Print resolved Jobtron locations")
    status.add_argument("--data-dir", type=Path, default=canonical_data_dir())

    uninstall = subparsers.add_parser("uninstall", help="Remove Jobtron client entries, not data")
    uninstall.add_argument("--clients", type=_parse_clients, default=["codex", "opencode", "claude"])
    uninstall.add_argument("--dry-run", action="store_true")

    print_config = subparsers.add_parser("print-config", help="Print a client configuration entry")
    print_config.add_argument("--client", choices=["codex", "opencode", "claude"], required=True)
    print_config.add_argument("--data-dir", type=Path, default=canonical_data_dir())

    args = parser.parse_args()
    try:
        if args.action == "install":
            data_dir = args.data_dir.expanduser().resolve()
            if not args.dry_run and not (data_dir / "app.db").exists() and (LEGACY_DATA_DIR / "app.db").exists():
                print(f"Migrating existing Jobtron data to {data_dir}")
                migrate_legacy_data(destination=data_dir)
            install_clients(args.clients, data_dir, args.dry_run)
            if args.dry_run:
                print(f"Dry run complete for {', '.join(args.clients)}; no configuration was changed.")
            else:
                print(f"Jobtron configured for {', '.join(args.clients)} with shared data at {data_dir}")
        elif args.action == "migrate":
            print(migrate_legacy_data(args.source, args.data_dir))
        elif args.action == "doctor":
            return 0 if doctor(args.data_dir.expanduser().resolve()) else 1
        elif args.action == "status":
            data_dir = args.data_dir.expanduser().resolve()
            print(f"canonical_data_dir: {canonical_data_dir()}")
            print(f"requested_data_dir: {data_dir}")
            print(f"runtime_data_dir: {resolved_data_dir()}")
            print(f"database_integrity: {_sqlite_integrity(data_dir / 'app.db')}")
            print(f"opencode_config: {_opencode_config_path()}")
        elif args.action == "uninstall":
            uninstall_clients(args.clients, args.dry_run)
            print("Removed requested Jobtron client entries. Application data was not deleted.")
        else:
            data_dir = args.data_dir.expanduser().resolve()
            if args.client == "opencode":
                print(json.dumps({"jobtron6900": opencode_entry(data_dir)}, indent=2))
            else:
                environment = _client_environment(data_dir, args.client)
                print(json.dumps({"command": _uv_server_command(), "env": environment}, indent=2))
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"jobtron-setup: {exc}\n")
