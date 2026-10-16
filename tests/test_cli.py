from __future__ import annotations

import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app import cli
from app.config import REPOSITORY_ROOT


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    port = _free_port()
    monkeypatch.setenv("JOBTRON_DATA_DIR", str(data_dir))
    monkeypatch.setenv("JOBTRON_HOST", "127.0.0.1")
    monkeypatch.setenv("JOBTRON_PORT", str(port))
    monkeypatch.setenv("JOBTRON_CLIENT", "pytest")
    yield data_dir, port
    owner = cli.read_owner(cli.default_settings())
    pid = owner.get("pid")
    if isinstance(pid, int) and cli._pid_alive(pid):
        import os
        import signal

        os.kill(pid, signal.SIGTERM)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPOSITORY_ROOT,
        timeout=60,
    )


def test_start_status_stop_lifecycle(cli_env):
    data_dir, port = cli_env
    assert run_cli("status").returncode == 1

    started = run_cli("start")
    assert started.returncode == 0, started.stderr
    assert f"http://127.0.0.1:{port}/applications" in started.stdout

    status = run_cli("status")
    assert status.returncode == 0
    assert "running" in status.stdout
    assert f"http://127.0.0.1:{port}/applications" in status.stdout
    assert "Agent registration:" in status.stdout
    assert (data_dir / "jobtron.log").exists()

    again = run_cli("start")
    assert again.returncode == 0
    assert "already running" in again.stdout

    stopped = run_cli("stop")
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert "Stopped" in stopped.stdout

    after = run_cli("status")
    assert after.returncode == 1
    assert "not running" in after.stdout
    assert run_cli("stop").stdout.startswith("Jobtron web UI is not running")


def test_start_reports_port_conflict_without_changing_port(cli_env):
    _, port = cli_env
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.listen()
    held = threading.Event()

    def accept_and_close() -> None:
        while not held.is_set():
            listener.settimeout(0.2)
            try:
                connection, _ = listener.accept()
            except OSError:
                continue
            connection.close()

    thread = threading.Thread(target=accept_and_close, daemon=True)
    thread.start()
    try:
        result = run_cli("start")
        assert result.returncode == 1
        assert str(port) in result.stderr
        assert "JOBTRON_PORT" in result.stderr
        assert "port conflict" in run_cli("status").stdout
        assert run_cli("stop").returncode == 1
    finally:
        held.set()
        thread.join()
        listener.close()


def test_ensure_running_writes_nothing_to_stdout(cli_env, capfd):
    settings = cli.default_settings()
    info = cli.ensure_running(settings)
    try:
        assert not info["already_running"]
        assert info["pid"] == cli.read_owner(settings)["pid"]
        assert cli.ensure_running(settings)["already_running"]
    finally:
        cli.stop_running(settings)
    assert capfd.readouterr().out == ""


def test_missing_subcommand_prints_help(capsys):
    assert cli.main([]) == 2
    assert "usage: jobtron" in capsys.readouterr().out


def test_concurrent_starts_share_one_ui(cli_env) -> None:
    """Agent clients often launch several MCP sessions at once; all must succeed against one UI."""
    starters = [
        subprocess.Popen(
            [sys.executable, "-m", "app.cli", "start"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=REPOSITORY_ROOT,
        )
        for _ in range(4)
    ]
    results = [(starter.wait(timeout=60), starter.stdout.read()) for starter in starters]
    assert [code for code, _ in results] == [0, 0, 0, 0]
    assert sum("already running" in output for _, output in results) == 3
    log = (cli_env[0] / "jobtron.log").read_text() if (cli_env[0] / "jobtron.log").exists() else ""
    assert "already in use" not in log
