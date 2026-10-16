from __future__ import annotations

import asyncio
import json
import os
import select
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from app import cli
from app.config import Settings
from app.mcp.server import build_server

MCP_COMMAND = Path(sys.executable).with_name("jobtron-mcp")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _spawn(data_dir: Path, port: int, client: str) -> subprocess.Popen:
    return subprocess.Popen(
        [MCP_COMMAND],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={
            **os.environ,
            "JOBTRON_DATA_DIR": str(data_dir),
            "JOBTRON_PORT": str(port),
            "JOBTRON_CLIENT": client,
        },
    )


def _tool_count(process: subprocess.Popen) -> int:
    def send(message: dict) -> None:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def read() -> dict:
        ready, _, _ = select.select([process.stdout], [], [], 30)
        assert ready, "no MCP response within 30s"
        return json.loads(process.stdout.readline())

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "startup-test", "version": "1"}}})
    assert read()["result"]["serverInfo"]["name"] == "jobtron6900"
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    return len(read()["result"]["tools"])


def _close(process: subprocess.Popen) -> str:
    if process.stdin and not process.stdin.closed:
        process.stdin.close()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    return process.stderr.read()


def _health(port: int) -> dict:
    deadline = time.monotonic() + 30
    while True:
        try:
            return json.loads(urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5).read())  # noqa: S310
        except OSError:
            assert time.monotonic() < deadline, "the UI never came up"
            time.sleep(0.2)


def test_mcp_session_starts_a_detached_ui_that_outlives_it(tmp_path):
    port = _free_port()
    settings = Settings(tmp_path, port=port)
    process = _spawn(tmp_path, port, "codex")
    other = None
    try:
        assert _tool_count(process) > 0
        assert _health(port)["status"] == "ready"
        record = tmp_path / "sessions" / f"{process.pid}.json"
        assert json.loads(record.read_text())["client"] == "codex"

        other = _spawn(tmp_path, port, "claude")
        assert _tool_count(other) > 0
        status = subprocess.run(
            [sys.executable, "-m", "app.cli", "status"],
            capture_output=True, text=True, timeout=60, cwd=cli.REPOSITORY_ROOT,
            env={**os.environ, "JOBTRON_DATA_DIR": str(tmp_path), "JOBTRON_PORT": str(port)},
        )
        assert "MCP sessions: 2 online (claude ×1, codex ×1)" in status.stdout
        assert "running" in status.stdout

        _close(other)
        _close(process)
        assert not record.exists()
        assert not (tmp_path / "sessions" / f"{other.pid}.json").exists()
        assert _health(port)["status"] == "ready"
        assert cli.live_sessions(settings) == []
        assert cli._sessions_line(settings) == "MCP sessions: none online"
    finally:
        for item in (process, other):
            if item is not None and item.poll() is None:
                item.kill()
        cli.stop_running(settings)


def test_mcp_serves_tools_and_explains_when_the_port_is_foreign(tmp_path):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        process = _spawn(tmp_path, port, "codex")
        first_line = ""
        try:
            assert _tool_count(process) > 0
            ready, _, _ = select.select([process.stderr], [], [], 15)
            assert ready, "no explanation on stderr within 15s"
            first_line = process.stderr.readline()
            assert process.poll() is None
            assert not Settings(tmp_path, port=port).owner_path.exists()
        finally:
            stderr = first_line + _close(process)
    assert f"Port {port}" in stderr
    assert "JOBTRON_PORT" in stderr
    assert "MCP tools still work" in stderr


def test_ui_tools_report_a_frontend_warning_instead_of_raising(services):
    def failing() -> None:
        raise cli.PortConflictError("Port 1 is busy.")

    calls: list[str] = []
    healthy = build_server(services, "http://127.0.0.1:1", lambda: calls.append("ensure"))
    broken = build_server(services, "http://127.0.0.1:1", failing)
    plain = build_server(services, "http://127.0.0.1:1")
    application = services.applications.create("Acme", "Engineer", job_description="Build things")

    def call(server, name, arguments):
        return asyncio.run(server.call_tool(name, arguments)).structured_content

    for name, arguments in (
        ("ui.show_dashboard", {}),
        ("ui.show_applications", {}),
        ("ui.show_application", {"application_id": application["id"]}),
        ("ui.show_setup", {}),
    ):
        result = call(broken, name, arguments)
        assert result["frontend_warning"] == "Port 1 is busy."
        assert result["frontend_url"].startswith("http://127.0.0.1:1/")
        assert "frontend_warning" not in call(healthy, name, arguments)
        assert "frontend_warning" not in call(plain, name, arguments)
    assert len(calls) == 4


def test_claude_project_scoped_registration_is_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    config = tmp_path / "claude" / ".claude.json"
    config.parent.mkdir()

    config.write_text(json.dumps({"projects": {"/work/a": {"mcpServers": {"other": {}}}}}))
    assert cli.agent_registration()["claude"] == "no"

    config.write_text(json.dumps({"projects": {"/work/a": {"mcpServers": {"jobtron6900": {}}}}}))
    assert cli.agent_registration()["claude"] == "yes (project)"

    config.write_text(json.dumps({"mcpServers": {"jobtron6900": {}}, "projects": {}}))
    assert cli.agent_registration()["claude"] == "yes"


def test_stale_session_records_are_not_counted_and_are_removed(tmp_path):
    settings = Settings(tmp_path)
    directory = cli.sessions_dir(settings)
    directory.mkdir()
    (directory / "999999999.json").write_text(json.dumps({"pid": 999999999, "client": "codex"}))
    (directory / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "client": "codex"}))
    (directory / "garbage.json").write_text("not json")
    assert cli.live_sessions(settings) == []
    assert not (directory / "999999999.json").exists()
    assert not (directory / f"{os.getpid()}.json").exists()


def test_ensure_running_reaps_the_ui_process_when_it_exits(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRON_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JOBTRON_PORT", str(_free_port()))
    settings = cli.default_settings()
    pid = cli.ensure_running(settings)["pid"]
    try:
        os.kill(pid, 15)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and Path(f"/proc/{pid}").exists():
            time.sleep(0.1)
        assert not Path(f"/proc/{pid}").exists(), "exited UI process was left as a zombie"
    finally:
        if cli._pid_alive(pid):
            os.kill(pid, 9)


def test_mcp_handshake_does_not_wait_for_the_ui_to_start(tmp_path):
    script = (
        "import time\n"
        "import app.mcp.server as server\n"
        "server.ensure_running = lambda settings: time.sleep(20)\n"
        "server.run_stdio()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=cli.REPOSITORY_ROOT,
        env={**os.environ, "JOBTRON_DATA_DIR": str(tmp_path), "JOBTRON_PORT": str(_free_port())},
    )
    try:
        started = time.monotonic()
        assert _tool_count(process) > 0
        assert time.monotonic() - started < 15
        assert (tmp_path / "sessions" / f"{process.pid}.json").exists()
    finally:
        _close(process)
