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

import pytest
from fastapi.testclient import TestClient

from app.cli import probe, stop_running
from app.config import (
    InstanceInUseError,
    InstanceLease,
    Settings,
    install_clients,
    migrate_legacy_data,
    opencode_entry,
)
from app.db import Database
from app.main import create_app
from app.mcp.ui import APPLICATIONS_UI, build_applications_ui


def test_web_notebook_workflow(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        guide = client.get('/guide')
        assert guide.status_code == 200
        assert 'Install Jobtron as an MCP server' in guide.text
        assert 'Codex' in guide.text and 'OpenCode' in guide.text and 'Claude Code' in guide.text
        assert 'jobtron-setup install' in guide.text
        client.post('/profile/resume', files={'file':('resume.txt',b'I build things.','text/plain')},data={'approved':'on'})
        response=client.post('/applications',data={'company_name':'Acme','formal_title':'Engineer','job_description':'Make things'},follow_redirects=False)
        assert response.status_code == 303
        workspace = response.headers['location']
        app_id = int(workspace.rsplit('/',1)[1])
        client.post(workspace+'/questions',data={'question':'Why you?','required':'on'})
        question_id=app.state.services.questions.list(app_id)[0]['id']
        client.post(f'{workspace}/questions/{question_id}/answers',data={'content':'Because.','approved':'on'})
        client.post(workspace+'/questions/reviewed',data={'questions_reviewed':'on'})
        html=client.get(workspace).text
        assert 'Why you?' in html and 'Because.' in html
        assert 'Prepare agent request' in html and 'Autofill with agent' not in html
        plan=client.post(workspace+'/drafts',headers={'accept':'application/json'})
        assert plan.status_code == 200 and plan.json()['valid']
        assert 'Never generate or change a resume' in plan.json()['agent_prompt']


def test_mcp_surface_matches_tracker_scope(settings):
    app=create_app(settings)
    tools={tool.name:tool for tool in asyncio.run(app.state.mcp.list_tools())}
    assert {'candidate.get_context','applications.list','applications.get_context','applications.progress','applications.approve_drafts','applications.set_questions_reviewed','applications.set_active','applications.prepare_drafts','applications.create_bundle','applications.list_bundles','questions.add','answers.save','question_bank.search','question_bank.apply','documents.save','documents.approve','ui.show_dashboard','ui.show_applications','ui.show_application'} <= tools.keys()
    assert not {'applications.prepare_autofill','applications.autofill_options','applications.attention','events.add','documents.academic'} & tools.keys()
    for name in ('ui.show_applications','ui.show_application'):
        assert tools[name].meta['ui']['resourceUri']=='ui://jobtron/applications.html'
        assert tools[name].meta['openai/outputTemplate']=='ui://jobtron/applications.html'
    resources=asyncio.run(app.state.mcp.list_resource_templates())
    assert any(str(resource.uri_template)=='jobtron://documents/{document_id}' for resource in resources)
    static_resources=asyncio.run(app.state.mcp.list_resources())
    assert any(str(resource.uri)=='jobtron://dashboard' for resource in static_resources)
    review=next(resource for resource in static_resources if str(resource.uri)=='ui://jobtron/applications.html')
    assert review.mime_type=='text/html;profile=mcp-app'
    assert 'ui/initialize' in APPLICATIONS_UI
    assert 'Draft — needs review' in APPLICATIONS_UI
    assert 'http://localhost:9876/applications' in build_applications_ui('http://localhost:9876')


def test_detail_page_keeps_reused_answer_visible_for_review(settings):
    app=create_app(settings)
    services=app.state.services
    application=services.applications.create('Acme','Engineer',job_description='Build things')
    question=services.questions.add(application['id'],'Are you authorized to work?',required_field=True)
    entry=services.questions.save_bank_entry('Are you authorized to work?','Yes.',approved=True)
    services.questions.apply_bank_answer(question['id'],entry['id'],approved=False,source_label='question_bank:auto')
    with TestClient(app) as client:
        html=client.get(f"/applications/{application['id']}").text
    assert 'Yes.</textarea>' in html
    assert '>Draft<' in html
    assert 'I reviewed this answer and approve it' in html


def test_approve_drafts_over_mcp_refuses_a_draft_from_another_application(settings):
    app=create_app(settings)
    services=app.state.services
    mine=services.applications.create("Acme","Engineer",job_description="Build things")
    other=services.applications.create("Other Co","Engineer",job_description="Elsewhere")
    theirs=services.documents.save("application_resume","Theirs",other["id"],"Resume",approved=False)

    result=asyncio.run(app.state.mcp.call_tool("applications.approve_drafts",{"application_id":mine["id"],"document_ids":[theirs["id"]]}))

    assert result.structured_content["refused"] is True
    assert result.structured_content["approved_documents"]==[]
    assert services.documents.get(theirs["id"])["approved"]==0


def test_agent_presented_ui_returns_authoritative_review(settings):
    app=create_app(settings)
    services=app.state.services
    application=services.applications.create('Acme','Engineer',job_description='Build things')
    collection=asyncio.run(app.state.mcp.call_tool('ui.show_applications',{'application_ids':[application['id']]}))
    assert collection.structured_content['view']=='applications'
    assert collection.structured_content['frontend_url']=='http://127.0.0.1:8765/applications'
    assert collection.structured_content['applications'][0]['formal_title']=='Engineer'
    review=asyncio.run(app.state.mcp.call_tool('ui.show_application',{'application_id':application['id'],'reason':'created'}))
    assert review.structured_content['view']=='application'
    assert review.structured_content['frontend_url']==f"http://127.0.0.1:8765/applications/{application['id']}"
    assert review.structured_content['reason_label']=='Application created'
    assert review.structured_content['application']['company_name']=='Acme'
    assert review.structured_content['progress']['next_action']['requires_user_input'] is True
    assert 'autofill' not in review.structured_content
    services.documents.save('master_resume','Original',text_content='My resume',approved=True)
    services.applications.set_questions_reviewed(application['id'],True)
    plan=asyncio.run(app.state.mcp.call_tool('applications.prepare_drafts',{'application_id':application['id']}))
    assert plan.structured_content['valid'] is True
    assert 'Never generate or change a resume' in plan.structured_content['agent_prompt']
    assert '<form' not in APPLICATIONS_UI
    assert 'applications.prepare_autofill' not in APPLICATIONS_UI
    assert 'Draft — needs review' in APPLICATIONS_UI


def _stop_ui(data_dir, port):
    settings = Settings(data_dir, port=port)
    deadline = time.monotonic() + 5
    while probe(settings) != "ours" and time.monotonic() < deadline:
        time.sleep(0.2)
    stop_running(settings)


def _wait_for_ui(port):
    deadline = time.monotonic() + 30
    while True:
        try:
            return json.loads(urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5).read())  # noqa: S310
        except OSError:
            assert time.monotonic() < deadline, "the UI never came up"
            time.sleep(0.2)


def test_installed_stdio_entrypoint_completes_mcp_handshake(tmp_path):
    command = Path(sys.executable).with_name("jobtron-mcp")
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "jobtron-test", "version": "1"},
        },
    }
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    environment = {
        **os.environ,
        "JOBTRON_DATA_DIR": str(tmp_path),
        "JOBTRON_PORT": str(port),
        "JOBTRON_CLIENT": "test",
    }
    completed = subprocess.run(
        [command],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        timeout=8,
        check=True,
        env=environment,
    )
    _stop_ui(tmp_path, port)
    response = json.loads(completed.stdout)
    assert response["result"]["serverInfo"]["name"] == "jobtron6900"
    instructions = response["result"]["instructions"]
    assert "Never create a custom resume" in instructions[:512]
    assert "ui.show_application" in instructions and "frontend_url" in instructions
    assert "workflow.next" in instructions
    assert "applications.prepare_drafts" in instructions
    assert "new unapproved version" in instructions
    assert "review" in instructions.lower()
    assert len(instructions) <= 2000


def test_original_resume_upload_extracts_and_remains_downloadable(settings):
    app=create_app(settings)
    with TestClient(app) as client:
        response=client.post('/profile/resume',data={'title':'Original resume','approved':'on'},files={'file':('resume.txt',b'BSc Computer Engineering','text/plain')},follow_redirects=False)
        assert response.status_code==303
        html=client.get('/profile').text
        assert 'Original resume' in html
        assert 'Selected for new applications' in html
        assert 'Academic documents' not in html
        resume=app.state.services.documents.list(None,'master_resume')[0]
        assert resume['text_content']=='BSc Computer Engineering'
        assert client.get(f"/profile/resume/{resume['id']}").content==b'BSc Computer Engineering'


def test_dashboard_tool_health_and_instruction_budget(settings):
    app = create_app(settings)
    app.state.services.applications.create("Dashboard Co", "Platform Engineer")
    with TestClient(app) as client:
        health = client.get("/healthz").json()
        assert health["service"] == "jobtron6900"
        assert health["status"] == "ready"
        assert health["frontend_url"] == "http://127.0.0.1:8765"

    result = asyncio.run(app.state.mcp.call_tool("ui.show_dashboard", {}))
    assert result.structured_content["view"] == "applications"
    assert result.structured_content["frontend_url"] == "http://127.0.0.1:8765/applications"
    assert result.structured_content["applications"][0]["company_name"] == "Dashboard Co"

    instructions = app.state.mcp._lowlevel_server.create_initialization_options().instructions
    assert "Never create a custom resume" in instructions[:512]
    assert "ui.show_application" in instructions and "frontend_url" in instructions
    assert len(instructions) <= 2000


def test_single_instance_lease_rejects_a_second_client_and_releases(tmp_path):
    shared = tmp_path / "shared"
    codex = Settings(shared, client_name="codex")
    opencode = Settings(shared, client_name="opencode")

    with InstanceLease(codex):
        owner = json.loads(codex.owner_path.read_text())
        assert owner["client"] == "codex"
        with pytest.raises(InstanceInUseError, match="already in use by codex"):
            with InstanceLease(opencode):
                pass

    assert not codex.owner_path.exists()
    with InstanceLease(opencode):
        assert json.loads(opencode.owner_path.read_text())["client"] == "opencode"


def test_legacy_data_migration_is_verified_copied_and_not_overwritten(tmp_path):
    source = tmp_path / "legacy"
    destination = tmp_path / "canonical"
    database = Database(source / "app.db")
    database.initialize()
    database.execute("UPDATE candidates SET name = ? WHERE id = 1", ("Migration Test",))
    files = source / "files"
    files.mkdir()
    (files / "resume.txt").write_text("resume", encoding="utf-8")

    with InstanceLease(Settings(source, client_name="migration-owner")):
        with pytest.raises(RuntimeError, match="currently using"):
            migrate_legacy_data(source, destination)

    migrated = migrate_legacy_data(source, destination)
    assert migrated == destination.resolve()
    candidate = Database(destination / "app.db").one("SELECT name FROM candidates WHERE id = 1")
    assert candidate is not None
    assert candidate["name"] == "Migration Test"
    assert (destination / "files" / "resume.txt").read_text(encoding="utf-8") == "resume"
    assert (source / "app.db").exists()

    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        migrate_legacy_data(source, destination)


def test_opencode_entry_uses_the_shared_data_and_common_stdio_entrypoint(tmp_path):
    entry = opencode_entry(tmp_path)
    assert entry["type"] == "local"
    assert entry["enabled"] is True
    assert entry["environment"]["JOBTRON_DATA_DIR"] == str(tmp_path.resolve())
    assert entry["environment"]["JOBTRON_CLIENT"] == "opencode"
    assert entry["command"][-1] == "jobtron-mcp"
    assert "--directory" in entry["command"]


def _mcp_handshake(stream_in, stream_out, name):
    """initialize + tools/list over a live stdio pipe. Returns the tool count."""
    def send(message):
        stream_in.write(json.dumps(message) + "\n")
        stream_in.flush()

    def read(timeout):
        ready, _, _ = select.select([stream_out], [], [], timeout)
        assert ready, f"{name}: no response within {timeout}s"
        return json.loads(stream_out.readline())

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": name, "version": "1"}}})
    assert read(20)["result"]["serverInfo"]["name"] == "jobtron6900"
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    return len(read(20)["result"]["tools"])


def test_stdio_second_client_shares_the_frontend_and_serves_its_own_tools(tmp_path):
    """Every MCP client gets a process; they share one database and one detached web UI.

    A second client used to exit(2) on the instance lease, which clients render
    as "connected (0 tools)" because the explanation only reaches stderr.
    """
    command = Path(sys.executable).with_name("jobtron-mcp")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    environment = {
        **os.environ,
        "JOBTRON_DATA_DIR": str(tmp_path / "shared"),
        "JOBTRON_PORT": str(port),
        "JOBTRON_CLIENT": "codex-test",
    }

    def spawn(client):
        return subprocess.Popen(
            [command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            env={**environment, "JOBTRON_CLIENT": client},
        )

    first = spawn("codex-test")
    second = None
    try:
        first_tools = _mcp_handshake(first.stdin, first.stdout, "codex-test")
        assert first_tools > 0

        second = spawn("claude-test")
        second_tools = _mcp_handshake(second.stdin, second.stdout, "claude-test")
        assert second_tools == first_tools, "a second client must serve the same tools"
        assert second.poll() is None, "a second client must not exit"

        health = _wait_for_ui(port)
        assert health["service"] == "jobtron6900"
        assert health["status"] == "ready"
    finally:
        for process in (first, second):
            if process is None:
                continue
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    handed_off = subprocess.run(
        [command], input=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "opencode-test", "version": "1"}}}) + "\n",
        text=True, capture_output=True, timeout=25,
        env={**environment, "JOBTRON_CLIENT": "opencode-test"}, check=True,
    )
    _stop_ui(tmp_path / "shared", port)
    assert json.loads(handed_off.stdout)["result"]["serverInfo"]["name"] == "jobtron6900"


def test_opencode_installer_preserves_unrelated_global_configuration(tmp_path, monkeypatch):
    config_root = tmp_path / "config"
    config_path = config_root / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"model": "local/test", "mcp": {"another": {"type": "remote", "url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))

    install_clients(["opencode"], tmp_path / "shared")

    installed = json.loads(config_path.read_text(encoding="utf-8"))
    assert installed["model"] == "local/test"
    assert "another" in installed["mcp"]
    assert installed["mcp"]["jobtron6900"]["environment"]["JOBTRON_DATA_DIR"] == str((tmp_path / "shared").resolve())
    assert config_path.with_suffix(".json.jobtron-backup").exists()


def test_stdio_serves_tools_even_when_the_port_belongs_to_something_else(tmp_path):
    """A busy port must not cost the user their tools; the UI stays off and the port is never changed."""
    command = Path(sys.executable).with_name("jobtron-mcp")
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        environment = {
            **os.environ,
            "JOBTRON_DATA_DIR": str(tmp_path),
            "JOBTRON_PORT": str(port),
            "JOBTRON_CLIENT": "port-test",
        }
        process = subprocess.Popen(
            [command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=environment,
        )
        try:
            assert _mcp_handshake(process.stdin, process.stdout, "port-test") > 0
            assert process.poll() is None
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
