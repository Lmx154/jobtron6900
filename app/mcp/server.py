from __future__ import annotations

import asyncio
import socket
import sys
from contextlib import asynccontextmanager
from threading import Thread
from typing import Any
from urllib.parse import urlencode

import anyio
import mcp_types as types
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.shared.message import SessionMessage

from app.config import Settings, default_settings
from app.db import Database
from app.mcp.ui import APPLICATIONS_UI, APPLICATIONS_UI_URI
from app.services import Services
from app.storage import FileStorage


@asynccontextmanager
async def compatible_stdio_server():
    """MCP stdio transport using asyncio pipes, which work on Python 3.14."""
    read_send,read_stream=anyio.create_memory_object_stream[SessionMessage|Exception](0)
    write_stream,write_receive=anyio.create_memory_object_stream[SessionMessage](0)

    async def read_stdin() -> None:
        reader=asyncio.StreamReader(); protocol=asyncio.StreamReaderProtocol(reader)
        await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin)
        async with read_send:
            while line := await reader.readline():
                try: message=types.jsonrpc_message_adapter.validate_json(line,by_name=False)
                except Exception as exc: await read_send.send(exc); continue
                await read_send.send(SessionMessage(message))

    async def write_stdout() -> None:
        async with write_receive:
            async for session_message in write_receive:
                payload=session_message.message.model_dump_json(by_alias=True,exclude_unset=True)
                sys.stdout.write(payload+"\n"); sys.stdout.flush()
                await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(read_stdin); task_group.start_soon(write_stdout)
        try: yield read_stream,write_stream
        finally: task_group.cancel_scope.cancel()


def build_server(services: Services, frontend_base_url: str = "http://127.0.0.1:8765") -> MCPServer:
    frontend_base_url=frontend_base_url.rstrip("/")
    mcp=MCPServer("jobtron6900",instructions="""Jobtron is the user's local source of truth for job applications.

APPLICATION WORKFLOW (mandatory): When the user supplies a posting or URL, retrieve the available posting details, create or update the application, and continue the workflow instead of stopping at data entry. After every application-related write, call applications.progress. Report percent_complete and blocking_items, then follow next_action. If next_action.requires_user_input is true, end with its exact question. If it is false, perform its instruction, call applications.progress again, and continue until user input is required or the package is complete. Never claim an application is complete without applications.progress returning ready_to_apply=true.

PRESENTATION WORKFLOW (mandatory): Jobtron is agent-driven and user-confirmed. The conversation is the control plane; UI is a review artifact, never the starting point. When the user asks to see, browse, review, or compare applications, call applications.list first and then ui.show_applications. After creating or materially changing an application package, finish the work and call ui.show_application so the user sees the resulting authoritative state and can confirm or request revision. Both presentation tools return frontend_url: always include that exact URL as a clickable link in the response, including when the in-chat MCP App renders successfully. On every subsequent material step, call ui.show_application again and send its refreshed frontend_url so the linked frontend reflects the authoritative state. When an autofill wizard submits an applications.prepare_autofill agent_prompt, follow that exact source and target selection through the normal read, draft, save, progress, and presentation workflow. Do not show UI for incidental reads or intermediate writes. Do not ask the user to start or manually maintain a standalone workstation; the stdio entrypoint hosts the fallback frontend.

Start existing work with applications.list and applications.get_context. Before applications.update, read the current record and preserve unchanged fields. Capture every proprietary question, then call applications.set_questions_reviewed; if there are no questions, explicitly record reviewed=true. Before drafting an application answer, call question_bank.search using the question's important words. Reuse a suitable entry with question_bank.apply; after the user approves a durable answer, save it to the bank for future wording variants.

Candidate context includes academic_documents and application_context_documents. For every enabled academic document, use its extracted text or read its resource_uri, then call documents.mark_considered so the application records that the source was taken into account. Do not invent facts when extraction is empty, unsupported, or failed; ask the user for readable text.

Write only when the user asks. Save answer and document revisions as new versions, unapproved unless approval is explicit. Mark Applied only after the user confirms external submission; this creates an immutable archive. Use resource_uri values to read uploaded files.""")

    @mcp.resource(
        APPLICATIONS_UI_URI,
        name="Jobtron application review",
        title="Jobtron application review",
        description="Agent-presented application list and confirmation surface.",
        mime_type="text/html;profile=mcp-app",
        meta={"ui":{"prefersBorder":False}},
    )
    def applications_ui() -> str:
        return APPLICATIONS_UI

    ui_meta={
        "ui":{"resourceUri":APPLICATIONS_UI_URI},
        "openai/outputTemplate":APPLICATIONS_UI_URI,
        "openai/toolInvocation/invoking":"Preparing your Jobtron review…",
        "openai/toolInvocation/invoked":"Jobtron review ready.",
    }

    @mcp.tool(
        name="ui.show_applications",
        title="Show Jobtron applications",
        description="Render a user-requested application collection for review. Call applications.list first, then pass its IDs. This tool presents authoritative state and returns a frontend_url that must be shared with the user; it does not mutate state.",
        meta=ui_meta,
        structured_output=True,
    )
    def ui_show_applications(application_ids:list[int]|None=None,status:str|None=None) -> dict[str,Any]:
        applications=services.applications.list(status)
        if application_ids is not None:
            selected=set(application_ids)
            applications=[application for application in applications if application["id"] in selected]
        counts={state:sum(application["status"]==state for application in applications) for state in services.applications.STATUSES}
        query=f"?{urlencode({'status':status})}" if status else ""
        return {
            "view":"applications",
            "title":"Your applications",
            "summary":"A reviewable snapshot of the application work Jobtron is managing with you.",
            "filter_label":status or "Current work",
            "frontend_url":f"{frontend_base_url}/applications{query}",
            "applications":applications,
            "counts":counts,
        }

    @mcp.tool(
        name="ui.show_application",
        title="Show one Jobtron application",
        description="Render one application's final authoritative state for user review after agent work, or when the user asks to inspect it. Call applications.get_context first. This tool presents state and returns a frontend_url that must be shared with the user; it does not mutate state.",
        meta=ui_meta,
        structured_output=True,
    )
    def ui_show_application(application_id:int,reason:str="review") -> dict[str,Any]:
        context=services.applications.get_context(application_id)
        reason_labels={
            "created":"Application created",
            "updated":"Changes ready for review",
            "approval":"Approval requested",
            "review":"Ready for review",
        }
        return {
            "view":"application",
            "reason":reason,
            "reason_label":reason_labels.get(reason,"Ready for review"),
            "review_summary":"Jobtron assembled the current authoritative state for your confirmation.",
            "frontend_url":f"{frontend_base_url}/applications/{application_id}",
            "application":context["application"],
            "progress":context["progress"],
            "autofill":services.applications.autofill_options(application_id),
            "question_count":len(context["questions"]),
            "document_count":len(context["documents"]),
        }

    @mcp.tool(name="candidate.get_profile")
    def candidate_get_profile() -> dict: """Return the primary candidate profile."""; return services.candidate.get_profile()
    @mcp.tool(name="candidate.get_facts")
    def candidate_get_facts() -> list[dict]: """Return reusable candidate facts."""; return services.candidate.get_facts()
    @mcp.tool(name="candidate.get_context")
    def candidate_get_context() -> dict: """Return profile, facts, and master resume versions."""; return services.candidate.get_context()
    @mcp.tool(name="candidate.add_fact")
    def candidate_add_fact(category:str,title:str,description:str,tags:str="",source:str="",verified:bool=False) -> dict: """Add a reusable candidate fact."""; return services.candidate.add_fact(category,title,description,tags,source,verified)
    @mcp.tool(name="candidate.update_fact")
    def candidate_update_fact(fact_id:int,category:str,title:str,description:str,tags:str="",source:str="",verified:bool=False) -> dict: """Replace the editable fields of a candidate fact."""; return services.candidate.update_fact(fact_id,category=category,title=title,description=description,tags=tags,source=source,verified=verified)

    @mcp.tool(name="applications.list")
    def applications_list(status:str|None=None) -> list[dict]: """List applications, optionally filtered by status."""; return services.applications.list(status)
    @mcp.tool(name="applications.get")
    def applications_get(application_id:int) -> dict: """Return one application record."""; return services.applications.get(application_id)
    @mcp.tool(name="applications.get_context")
    def applications_get_context(application_id:int) -> dict: """Return the complete coherent package for an application."""; return services.applications.get_context(application_id)
    @mcp.tool(name="applications.progress")
    def applications_progress(application_id:int) -> dict: """Return explicit completeness, blockers, and the single required next action/question."""; return services.applications.progress(application_id)
    @mcp.tool(name="applications.autofill_options",structured_output=True)
    def applications_autofill_options(application_id:int) -> dict[str,Any]: """Return current selectable sources and output targets for the application autofill wizard. This tool does not mutate state."""; return services.applications.autofill_options(application_id)
    @mcp.tool(name="applications.prepare_autofill",structured_output=True)
    def applications_prepare_autofill(application_id:int,source_ids:list[str],target_ids:list[str],custom_request:str="") -> dict[str,Any]: """Validate autofill wizard selections and return the exact agent_prompt for the normal Jobtron workflow. This tool does not mutate state."""; return services.applications.prepare_autofill(application_id,source_ids,target_ids,custom_request)
    @mcp.tool(name="applications.set_questions_reviewed")
    def applications_set_questions_reviewed(application_id:int,reviewed:bool=True) -> dict: """Record that all proprietary questions were inventoried, including when there are none."""; return services.applications.set_questions_reviewed(application_id,reviewed)
    @mcp.tool(name="applications.set_requirements")
    def applications_set_requirements(application_id:int,cover_letter_required:bool=False) -> dict: """Set application-specific material requirements and return refreshed progress."""; services.applications.update(application_id,cover_letter_required=cover_letter_required); return services.applications.progress(application_id)
    @mcp.tool(name="applications.create")
    def applications_create(company_name:str,formal_title:str,url:str="",location:str="",job_description:str="",normalized_role:str="",role_family:str="",notes:str="") -> dict: """Create an application."""; return services.applications.create(company_name,formal_title,url=url,location=location,job_description=job_description,normalized_role=normalized_role,role_family=role_family,notes=notes)
    @mcp.tool(name="applications.update")
    def applications_update(application_id:int,company_name:str,formal_title:str,normalized_role:str="",role_family:str="",url:str="",location:str="",job_description:str="",status:str="Draft",notes:str="") -> dict: """Update an application; marking it Applied creates its immutable archive."""; return services.applications.update(application_id,company_name=company_name,formal_title=formal_title,normalized_role=normalized_role,role_family=role_family,url=url,location=location,job_description=job_description,status=status,notes=notes)

    @mcp.tool(name="questions.list")
    def questions_list(application_id:int) -> list[dict]: """List application questions with answer history."""; return services.questions.list(application_id)
    @mcp.tool(name="questions.add")
    def questions_add(application_id:int,question:str,field_type:str="text",allowed_choices:str="",required:bool=False,character_limit:int|None=None,sort_order:int=0,bank_entry_id:int|None=None) -> dict: """Add an application question, optionally linked to a reusable bank entry."""; return services.questions.add(application_id,question,field_type,allowed_choices,required,character_limit,sort_order,bank_entry_id)
    @mcp.tool(name="questions.update")
    def questions_update(question_id:int,question:str,field_type:str="text",allowed_choices:str="",required:bool=False,character_limit:int|None=None,sort_order:int=0) -> dict: """Update an application question."""; return services.questions.update(question_id,question=question,field_type=field_type,allowed_choices=allowed_choices,required=required,character_limit=character_limit,sort_order=sort_order)
    @mcp.tool(name="answers.save")
    def answers_save(question_id:int,content:str,source_label:str="model",approved:bool=False,save_to_bank:bool=False,keywords:str="") -> dict: """Save a new immutable answer version and optionally make it reusable."""; return services.questions.save_answer(question_id,content,source_label,approved,save_to_bank,keywords)

    @mcp.tool(name="question_bank.search")
    def question_bank_search(query:str,limit:int=10) -> list[dict]: """Keyword-search approved reusable answers and rank wording variants by overlap."""; return services.questions.search_bank(query,limit)
    @mcp.tool(name="question_bank.save")
    def question_bank_save(canonical_question:str,answer:str,keywords:str="",approved:bool=True,bank_entry_id:int|None=None) -> dict: """Create or update a reusable canonical application answer."""; return services.questions.save_bank_entry(canonical_question,answer,keywords,approved,bank_entry_id)
    @mcp.tool(name="question_bank.apply")
    def question_bank_apply(question_id:int,bank_entry_id:int,approved:bool=False) -> dict: """Apply a reusable answer as a new version on an application question."""; return services.questions.apply_bank_answer(question_id,bank_entry_id,approved)

    @mcp.tool(name="documents.list")
    def documents_list(application_id:int|None=None,document_type:str|None=None) -> list[dict]: """List master or application document versions."""; return services.documents.list(application_id,document_type)
    @mcp.tool(name="documents.get")
    def documents_get(document_id:int) -> dict: """Return document metadata, text, and its file resource URI."""; document=services.documents.get(document_id); return {**document,"resource_uri":f"jobtron://documents/{document_id}"}
    @mcp.tool(name="documents.save")
    def documents_save(document_type:str,title:str,text_content:str,application_id:int|None=None,approved:bool=False,use_for_applications:bool=False) -> dict: """Save a new text document version; candidate academic documents can be enabled as application context."""; return services.documents.save(document_type,title,application_id,text_content,approved=approved,use_for_applications=use_for_applications)
    @mcp.tool(name="documents.approve")
    def documents_approve(document_id:int) -> dict: """Approve one document version and supersede prior approval in its scope."""; return services.documents.approve(document_id)
    @mcp.tool(name="documents.academic")
    def documents_academic(only_enabled:bool=False) -> list[dict]: """List candidate academic documents and their extraction/application-context flags."""; return services.documents.academic(only_enabled)
    @mcp.tool(name="documents.set_application_use")
    def documents_set_application_use(document_id:int,use_for_applications:bool) -> dict: """Enable or disable a candidate academic document as application context."""; return services.documents.set_application_use(document_id,use_for_applications)
    @mcp.tool(name="documents.mark_considered")
    def documents_mark_considered(application_id:int,document_id:int,note:str="") -> dict: """Record that an enabled academic document was taken into account for an application."""; return services.documents.mark_considered(application_id,document_id,note)
    @mcp.resource("jobtron://documents/{document_id}",mime_type="application/octet-stream")
    def document_resource(document_id:int) -> bytes: """Read exact uploaded document bytes, or UTF-8 for a text-only document."""; return services.documents.content(document_id)

    @mcp.tool(name="assets.list")
    def assets_list(application_id:int|None=None) -> list[dict]: """List uploaded assets; content is available through jobtron://assets/{id}."""; return services.assets.list(application_id)
    @mcp.tool(name="assets.get")
    def assets_get(asset_id:int) -> dict: """Return asset metadata and its MCP resource URI."""; asset=services.assets.get(asset_id); return {**asset,"resource_uri":f"jobtron://assets/{asset_id}"}
    @mcp.resource("jobtron://assets/{asset_id}",mime_type="application/octet-stream")
    def asset_resource(asset_id:int) -> bytes: """Read the exact bytes of an uploaded image, PDF, or other asset."""; return services.assets.content(asset_id)

    @mcp.tool(name="events.list")
    def events_list(application_id:int) -> list[dict]: """Return an application's timeline."""; return services.events.list(application_id)
    @mcp.tool(name="events.add")
    def events_add(application_id:int,event_type:str,occurred_at:str,notes:str="") -> dict: """Add an application event."""; return services.events.add(application_id,event_type,occurred_at,notes)
    @mcp.tool(name="events.update")
    def events_update(event_id:int,event_type:str,occurred_at:str,notes:str="") -> dict: """Update an application event."""; return services.events.update(event_id,event_type,occurred_at,notes)
    return mcp


def _start_frontend(settings: Settings) -> tuple[uvicorn.Server|None,Thread|None]:
    connect_host="127.0.0.1" if settings.host in {"0.0.0.0","::"} else settings.host
    try:
        with socket.create_connection((connect_host,settings.port),timeout=0.15):
            return None,None
    except OSError:
        pass

    frontend=uvicorn.Server(uvicorn.Config("app.main:app",host=settings.host,port=settings.port,log_level="warning",access_log=False))
    thread=Thread(target=frontend.run,name="jobtron-frontend",daemon=True)
    thread.start()
    return frontend,thread


def run_stdio() -> None:
    settings=default_settings(); db=Database(settings.database_path); db.initialize(); server=build_server(Services(db,FileStorage(settings.files_dir)),settings.frontend_base_url)
    frontend,frontend_thread=_start_frontend(settings)

    async def serve() -> None:
        async with compatible_stdio_server() as (read_stream,write_stream):
            await server._lowlevel_server.run(  # pyright: ignore[reportPrivateUsage]
                read_stream,write_stream,server._lowlevel_server.create_initialization_options()  # pyright: ignore[reportPrivateUsage]
            )

    try:
        anyio.run(serve)
    finally:
        if frontend is not None:
            frontend.should_exit=True
        if frontend_thread is not None:
            frontend_thread.join(timeout=2)
