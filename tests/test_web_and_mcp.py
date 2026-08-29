import asyncio
import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.mcp.ui import APPLICATIONS_UI


def test_web_notebook_workflow(settings):
    app=create_app(settings)
    with TestClient(app) as client:
        assert client.get("/").status_code==200
        guide=client.get("/guide")
        assert guide.status_code==200
        assert "Use Jobtron with Codex" in guide.text
        assert "applications.get_context" in guide.text
        assert "codex mcp get jobtron6900" in guide.text
        response=client.post("/applications",data={"company_name":"Acme","formal_title":"Engineer","job_description":"Make things"},follow_redirects=False)
        assert response.status_code==303
        workspace=response.headers["location"]
        assert "Engineer" in client.get(workspace).text
        app_id=int(workspace.rsplit("/",1)[1])
        client.post(f"{workspace}/questions",data={"question":"Why you?","field_type":"long_text","required":"on"})
        question_id=app.state.services.questions.list(app_id)[0]["id"]
        client.post(f"{workspace}/questions/{question_id}/answers",data={"content":"Because.","source_label":"manual","approved":"on"})
        client.post(f"{workspace}/assets",data={"title":"Screenshot","asset_type":"screenshot"},files={"file":("screen.png",b"pixels","image/png")})
        html=client.get(workspace).text
        assert "Why you?" in html and "Because." in html and "Screenshot" in html
        assert "Autofill with agent" in html
        assert 'class="question-card"' in html
        plan=client.post(f"{workspace}/autofill-plan",data={"sources":"job_posting","targets":["application_resume","application_questions"]})
        assert plan.status_code==200
        assert plan.json()["valid"] is True
        assert f"Autofill Jobtron application {app_id}" in plan.json()["agent_prompt"]


def test_mcp_surface_matches_blueprint(settings):
    app=create_app(settings)
    tools={tool.name:tool for tool in asyncio.run(app.state.mcp.list_tools())}
    assert {"candidate.get_context","applications.list","applications.get_context","applications.progress","applications.set_questions_reviewed","questions.add","answers.save","question_bank.search","question_bank.apply","documents.save","documents.approve","documents.academic","documents.mark_considered","assets.get","events.add","ui.show_applications","ui.show_application"} <= tools.keys()
    for name in ("ui.show_applications","ui.show_application"):
        assert tools[name].meta["ui"]["resourceUri"]=="ui://jobtron/applications.html"
        assert tools[name].meta["openai/outputTemplate"]=="ui://jobtron/applications.html"
    resources=asyncio.run(app.state.mcp.list_resource_templates())
    assert any(str(resource.uri_template)=="jobtron://assets/{asset_id}" for resource in resources)
    assert any(str(resource.uri_template)=="jobtron://documents/{document_id}" for resource in resources)
    static_resources=asyncio.run(app.state.mcp.list_resources())
    review=next(resource for resource in static_resources if str(resource.uri)=="ui://jobtron/applications.html")
    assert review.mime_type=="text/html;profile=mcp-app"
    assert "Presented by the agent" in APPLICATIONS_UI
    assert 'request("ui/initialize"' in APPLICATIONS_UI


def test_agent_presented_ui_returns_authoritative_review(settings):
    app=create_app(settings)
    application=app.state.services.applications.create("Acme","Engineer",job_description="Build things")

    collection=asyncio.run(app.state.mcp.call_tool("ui.show_applications",{"application_ids":[application["id"]]}))
    assert collection.structured_content["view"]=="applications"
    assert collection.structured_content["frontend_url"]=="http://127.0.0.1:8765/applications"
    assert collection.structured_content["applications"][0]["formal_title"]=="Engineer"

    review=asyncio.run(app.state.mcp.call_tool("ui.show_application",{"application_id":application["id"],"reason":"created"}))
    assert review.structured_content["view"]=="application"
    assert review.structured_content["frontend_url"]==f"http://127.0.0.1:8765/applications/{application['id']}"
    assert review.structured_content["reason_label"]=="Application created"
    assert review.structured_content["application"]["company_name"]=="Acme"
    assert review.structured_content["progress"]["next_action"]["requires_user_input"] is True
    assert review.structured_content["autofill"]["sources"][0]["id"]=="job_posting"

    options=asyncio.run(app.state.mcp.call_tool("applications.autofill_options",{"application_id":application["id"]}))
    assert options.structured_content["application_id"]==application["id"]
    plan=asyncio.run(app.state.mcp.call_tool("applications.prepare_autofill",{"application_id":application["id"],"source_ids":["job_posting"],"target_ids":["cover_letter"]}))
    assert plan.structured_content["valid"] is True
    assert "Do not mark the application Applied" in plan.structured_content["agent_prompt"]
    assert "<form" not in APPLICATIONS_UI
    assert "Open live frontend" in APPLICATIONS_UI
    assert 'request("tools/call"' in APPLICATIONS_UI
    assert "applications.prepare_autofill" in APPLICATIONS_UI


def test_installed_stdio_entrypoint_completes_mcp_handshake():
    command=Path(sys.executable).with_name("jobtron-mcp")
    request={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"jobtron-test","version":"1"}}}
    completed=subprocess.run([command],input=json.dumps(request)+"\n",text=True,capture_output=True,timeout=5,check=True)
    response=json.loads(completed.stdout)
    assert response["result"]["serverInfo"]["name"]=="jobtron6900"
    instructions=response["result"]["instructions"]
    assert "applications.get_context" in instructions
    assert "applications.progress" in instructions
    assert "end with its exact question" in instructions
    assert "agent-driven and user-confirmed" in instructions
    assert "ui.show_application" in instructions
    assert "frontend_url" in instructions


def test_academic_document_upload_extracts_and_displays_context_flag(settings):
    app=create_app(settings)
    with TestClient(app) as client:
        response=client.post(
            "/profile/academic-documents",
            data={"document_type":"academic_transcript","title":"Transcript","use_for_applications":"on"},
            files={"file":("transcript.txt",b"BSc Computer Engineering\nGPA 3.9","text/plain")},
            follow_redirects=False,
        )
        assert response.status_code==303
        html=client.get("/profile").text
        assert "Transcript" in html
        assert "GPA 3.9" in html
        assert "Application context" in html
