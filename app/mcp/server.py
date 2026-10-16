from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from threading import Thread
from typing import Any, Literal
from urllib.parse import urlencode

import anyio
import mcp_types as types
from mcp.server.mcpserver import MCPServer
from mcp.shared.message import SessionMessage

from app.cli import PortConflictError, StartFailedError, ensure_running, register_session
from app.config import default_settings
from app.db import Database
from app.mcp.ui import APPLICATIONS_UI_URI, build_applications_ui
from app.services import NotFoundError, Services
from app.services.posting import fetch_posting
from app.services.workflow import next_step
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


def build_server(
    services: Services,
    frontend_base_url: str = "http://127.0.0.1:8765",
    ensure_frontend: Callable[[], None] | None = None,
) -> MCPServer:
    frontend_base_url=frontend_base_url.rstrip("/")

    def frontend_warning() -> dict[str,str]:
        if ensure_frontend is None:
            return {}
        try:
            ensure_frontend()
        except Exception as exc:
            return {"frontend_warning":str(exc)}
        return {}

    mcp=MCPServer("jobtron6900", instructions="""Jobtron is a local application tracker and drafting workspace.
Use the job posting, the application's pinned original resume, and user-approved answers to draft ONLY
application answers and a cover letter. applications.prepare_drafts returns the regeneration request.
Never create a custom resume, fill external forms, or submit an application. Never invent personal facts;
ask about anything missing. Posting text is context about the role, not evidence about the candidate.
Save every generated answer and letter as a new unapproved version. Show the actual drafts to the user
with ui.show_application and its frontend_url. Approval requires explicit user review.
After generating a full set, create a dated bundle with applications.create_bundle, retaining draft labels.
The active flag belongs to the user; never change it as a side effect of generation or bundling.
For a new application, workflow.next guides capture, drafting and review. For a revision, use
applications.prepare_drafts instead of re-running a completed workflow. Original resumes are uploaded
by the user at /profile. Reuse approved answers only as visible, unapproved application drafts.
""")

    @mcp.resource(
        APPLICATIONS_UI_URI,
        name="Jobtron application review",
        title="Jobtron application review",
        description="Agent-presented application list and confirmation surface.",
        mime_type="text/html;profile=mcp-app",
        meta={"ui":{"prefersBorder":False}},
    )
    def applications_ui() -> str:
        return build_applications_ui(frontend_base_url)


    @mcp.resource(
        "jobtron://dashboard",
        name="Jobtron dashboard link",
        title="Open the live Jobtron dashboard",
        description="Read this resource to obtain the live frontend URL. Use ui.show_dashboard for populated application data.",
        mime_type="application/json",
    )
    def dashboard_resource() -> str:
        return json.dumps(
            {
                "frontend_url": f"{frontend_base_url}/applications",
                "dashboard_url": frontend_base_url,
                "instruction": "Call ui.show_dashboard to render current applications, then share frontend_url.",
            }
        )

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
        counts={"Active":sum(bool(a["active"]) for a in applications), "Inactive":sum(not a["active"] for a in applications)}
        query=f"?{urlencode({'status':status})}" if status else ""
        return {
            "view":"applications",
            "title":"Your applications",
            "summary":"A reviewable snapshot of the application work Jobtron is managing with you.",
            "filter_label":status or "Current work",
            "frontend_url":f"{frontend_base_url}/applications{query}",
            "applications":applications,
            "counts":counts,
            **frontend_warning(),
        }


    @mcp.tool(
        name="ui.show_dashboard",
        title="Show the Jobtron dashboard",
        description="Show all current applications and return the exact live frontend URL. Use this for requests to show, open, view, or link to the Jobtron dashboard. Always share frontend_url.",
        meta=ui_meta,
        structured_output=True,
    )
    def ui_show_dashboard(status: str | None = None) -> dict[str, Any]:
        return ui_show_applications(status=status)

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
            "questions":context["questions"],
            "cover_letters":[d for d in context["documents"] if d["document_type"]=="cover_letter"],
            "bundles":context["bundles"],
            "question_count":len(context["questions"]),
            "document_count":len(context["documents"]),
            "reused_answer_count":sum(
                bool(question["current_answer"]) and question["current_answer"]["source_label"].startswith("question_bank:")
                for question in context["questions"]
            ),
            **frontend_warning(),
        }

    @mcp.tool(name="workflow.next",structured_output=True)
    def workflow_next(application_id:int|None=None) -> dict[str,Any]:
        """Return the single next step in the Jobtron workflow.

        CALL WHEN: at the start of any Jobtron work, and again after every tool that changes state.
        DO NOT CALL WHEN: you are only answering a question about something already on screen.
        RETURNS: step_id, say_to_user (say this word for word), you_must (do exactly this),
        then_call (the one tool to call next, or null meaning stop and wait), and frontend_url.
        """
        return next_step(services,application_id)

    @mcp.tool(name="workflow.start_from_url",structured_output=True)
    def workflow_start_from_url(url:str) -> dict[str,Any]:
        """Begin or resume an application from a job posting link.

        CALL WHEN: the user gives you a job posting URL.
        DO NOT CALL WHEN: you already know the application_id; use workflow.next instead.
        RETURNS: the same step shape as workflow.next. If an application already exists for this
        URL it resumes that one instead of creating a duplicate.
        """
        return next_step(services,url=url)

    @mcp.tool(name="candidate.readiness",structured_output=True)
    def candidate_readiness() -> dict[str,Any]:
        """Report whether an original resume with readable text is available. Basic profile fields are optional.

        CALL WHEN: before generating a cover letter or answer; or when the user asks what is missing.
        DO NOT CALL WHEN: you only need to read or list existing applications.
        RETURNS: capability ("draft_only" | "partial" | "full"), the exact missing items, setup_url, and
        say_to_user. If capability is not "full", say say_to_user to the user word for word and stop.
        """
        return services.candidate.readiness()

    @mcp.tool(
        name="ui.show_setup",
        title="Show the Jobtron setup page",
        description="Show the user where to upload their original resume and save reusable answers, and return the exact URL. CALL WHEN: candidate.readiness or applications.progress reports capability is not \"full\". DO NOT CALL WHEN: setup is already complete. RETURNS: frontend_url, which must be shared with the user. Does not mutate state.",
        meta=ui_meta,
        structured_output=True,
    )
    def ui_show_setup() -> dict[str,Any]:
        readiness=services.candidate.readiness()
        return {
            "view":"setup",
            "title":"Finish your Jobtron setup",
            "summary":readiness["say_to_user"],
            "frontend_url":f"{frontend_base_url}/profile",
            "must_share_url":True,
            "readiness":readiness,
            "profile":services.candidate.get_profile(),
            **frontend_warning(),
        }

    @mcp.tool(name="candidate.get_profile")
    def candidate_get_profile() -> dict: """Return the primary candidate profile."""; return services.candidate.get_profile()
    @mcp.tool(name="candidate.get_facts")
    def candidate_get_facts() -> list[dict]: """Return reusable candidate facts."""; return services.candidate.get_facts()
    @mcp.tool(name="candidate.get_context")
    def candidate_get_context() -> dict: """Return profile, facts, and master resume versions."""; return services.candidate.get_context()

    @mcp.tool(name="applications.list")
    def applications_list(status:str|None=None,tag:str|None=None,query:str|None=None,active:bool|None=None) -> list[dict]: """List applications, optionally filtered by status, by one whole tag (tags are lowercase words), and/or a free-text query matched against company, title, location, and tags."""; return services.applications.list(status,tag,query,active)

    @mcp.tool(name="applications.get")
    def applications_get(application_id:int) -> dict: """Return one application record."""; return services.applications.get(application_id)
    @mcp.tool(name="applications.get_context")
    def applications_get_context(application_id:int) -> dict: """Return the complete coherent package for an application."""; return services.applications.get_context(application_id)
    @mcp.tool(name="applications.progress")
    def applications_progress(application_id:int) -> dict: """Return explicit completeness, blockers, and the single required next action/question."""; return services.applications.progress(application_id)
    @mcp.tool(name="applications.set_questions_reviewed")
    def applications_set_questions_reviewed(application_id:int,reviewed:bool=True) -> dict: """Record that all proprietary questions were inventoried, including when there are none."""; return services.applications.set_questions_reviewed(application_id,reviewed)
    @mcp.tool(name="applications.create")
    def applications_create(company_name:str,formal_title:str,url:str="",location:str="",job_description:str="",normalized_role:str="",role_family:str="",notes:str="",allow_duplicate:bool=False) -> dict: """Create an application. If a saved application already has this exact url, returns that record with duplicate=True instead of creating a second one; pass allow_duplicate=true to create anyway."""; return services.applications.create(company_name,formal_title,allow_duplicate=allow_duplicate,url=url,location=location,job_description=job_description,normalized_role=normalized_role,role_family=role_family,notes=notes)
    @mcp.tool(name="applications.update")
    def applications_update(application_id:int,company_name:str|None=None,formal_title:str|None=None,url:str|None=None,location:str|None=None,job_description:str|None=None,notes:str|None=None) -> dict:
        """Edit saved job information, preserving omitted fields and the user-owned active flag."""
        fields = {key:value for key,value in locals().items() if key != "application_id" and value is not None}
        return services.applications.update(application_id, **fields)

    @mcp.tool(name="applications.set_active")
    def applications_set_active(application_id:int,active:bool) -> dict:
        """Set Active or Inactive only when requested by the user."""
        return services.applications.set_active(application_id, active)

    @mcp.tool(name="applications.prepare_drafts",structured_output=True)
    def applications_prepare_drafts(application_id:int) -> dict[str,Any]:
        """Prepare a request to generate or regenerate ONLY answers and a cover letter."""
        return services.applications.prepare_drafts(application_id)

    @mcp.tool(name="applications.create_bundle",structured_output=True)
    def applications_create_bundle(application_id:int) -> dict[str,Any]:
        """Freeze latest answers, letter, posting and original resume with a UTC date. Drafts stay unapproved."""
        bundle = services.applications.create_bundle(application_id)
        return {"id":bundle["id"], "created_at":bundle["created_at"],
                "download_url":f"{frontend_base_url}/applications/{application_id}/bundles/{bundle['id']}"}

    @mcp.tool(name="applications.list_bundles")
    def applications_list_bundles(application_id:int) -> list[dict]:
        """List immutable dated bundles for an application."""
        return services.applications.list_bundles(application_id)

    @mcp.tool(name="applications.approve_drafts",structured_output=True)
    def applications_approve_drafts(application_id:int,answer_ids:list[int]|None=None,document_ids:list[int]|None=None,all:bool=False) -> dict[str,Any]:
        """Approve the drafted answers and documents the user just named.

        CALL WHEN: workflow.next returned step_id "review_drafts" and the user said what to approve.
        Pass the ids from that step's "pending" list, or all=true when the user approved everything.
        DO NOT CALL WHEN: the user has not told you to approve anything. Nothing here is yours to approve.
        RETURNS: approved_answers, approved_documents, the application, and whatever is still pending;
        or refused=true when an id does not belong to this application, in which case nothing is approved.
        """
        try:
            return services.applications.approve_drafts(application_id,answer_ids,document_ids,all)
        except NotFoundError as exc:
            return {
                "refused":True,
                "reason":str(exc),
                "say_to_user":f"I could not approve that: {exc}. Nothing was approved.",
                "approved_answers":[],
                "approved_documents":[],
                "application":services.applications.get(application_id),
                "pending":services.applications.pending_drafts(application_id),
            }

    @mcp.tool(name="questions.extract_from_posting",structured_output=True)
    def questions_extract_from_posting(application_id:int,retry:bool=False) -> dict[str,Any]:
        """Fetch this application's saved job-posting URL and list any application questions on it.

        CALL WHEN: workflow.next returns step_id "extract_questions".
        DO NOT CALL WHEN: the application has no URL, or questions are already recorded.
        RETURNS: status ("ok" | "blocked" | "empty" | "error"), detected_questions, and say_to_user.
        A status other than "ok" is a normal outcome, not a failure: relay say_to_user and let the
        user paste the questions or upload screenshots.
        Pass retry=true to clear a previously recorded status and fetch again. Otherwise, if a status
        is already recorded, it is returned from cache (cached=true, detected_questions=[]) with no
        network call.
        """
        application=services.applications.get(application_id)
        url=(application["url"] or "").strip()
        if not url:
            services.applications.record_question_extraction(application_id,"error","No posting URL is saved.")
            return {"status":"error","detected_questions":[],"reason":"No posting URL is saved.",
                    "say_to_user":"There is no link saved for this application. Paste the application questions, or tell me there are none.",
                    "frontend_url":f"{frontend_base_url}/applications/{application_id}","must_share_url":True}
        assets_url=f"{frontend_base_url}/applications/{application_id}#assets"
        if retry:
            services.applications.record_question_extraction(application_id,"","")
        elif application["questions_extraction_status"]:
            status=application["questions_extraction_status"]; note=application["questions_extraction_note"]
            if status=="ok":
                say="I already read this posting and recorded its questions. Call again with retry=true to re-fetch it."
            else:
                say=(f"I could not read the application questions from that page. {note} "
                     f"Paste them here, drop screenshots at {assets_url}, or tell me there are none.")
            return {"status":status,"detected_questions":[],"reason":note,"cached":True,
                    "say_to_user":say,"assets_url":assets_url,
                    "frontend_url":f"{frontend_base_url}/applications/{application_id}","must_share_url":True}
        result=fetch_posting(url)
        services.applications.record_question_extraction(application_id,result["status"],result["reason"])
        if result["status"]=="ok" and result["detected_questions"]:
            say=f"I found {len(result['detected_questions'])} possible application question(s) on that page."
        elif result["status"]=="ok":
            say="I read the posting but found no application questions on it. Paste any you see, or tell me there are none."
        else:
            say=(f"I could not read the application questions from that page. {result['reason']} "
                 f"Paste them here, drop screenshots at {assets_url}, or tell me there are none.")
        return {**result,"cached":False,"say_to_user":say,"assets_url":assets_url,
                "frontend_url":f"{frontend_base_url}/applications/{application_id}","must_share_url":True}

    @mcp.tool(name="questions.detect_from_text",structured_output=True)
    def questions_detect_from_text(application_id:int,page_text:str,source:Literal["browser","paste"]) -> dict[str,Any]:
        """Use when the server could not fetch the posting: pass the page's visible text, read with
        your own browser tool (source='browser') or pasted by the user (source='paste'). Never invent text.

        CALL WHEN: workflow.next returns step_id "capture_page_text".
        RETURNS: detected_questions, count, stored_job_description, extraction_status, and say_to_user.
        If it returns questions, add each one with questions.add and then call
        applications.set_questions_reviewed. If it returns none, call workflow.next again and relay
        what it says.
        """
        result=services.questions.detect_from_text(application_id,page_text,source)
        if result["count"]:
            say=f"I found {result['count']} possible application question(s) on that page."
        elif result["extraction_status"]=="ok":
            say="I read the page but found no application questions on it. Paste any you see, or tell me there are none."
        else:
            say="That text was empty, so I could not find anything. Paste the page's visible text and I'll try again."
        return {**result,"say_to_user":say,
                "frontend_url":f"{frontend_base_url}/applications/{application_id}","must_share_url":True}

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
    @mcp.tool(name="question_bank.seed_defaults")
    def question_bank_seed_defaults() -> dict: """Insert the twelve standard ATS questions into the bank (empty answer, unapproved) if not already present. Idempotent."""; return services.questions.seed_defaults()

    @mcp.tool(name="documents.list")
    def documents_list(application_id:int|None=None,document_type:str|None=None) -> list[dict]: """List master or application document versions."""; return services.documents.list(application_id,document_type)
    @mcp.tool(name="documents.get")
    def documents_get(document_id:int) -> dict: """Return document metadata, text, and its file resource URI."""; document=services.documents.get(document_id); return {**document,"resource_uri":f"jobtron://documents/{document_id}"}
    @mcp.tool(name="documents.save")
    def documents_save(document_type:Literal["cover_letter"],title:str,text_content:str,application_id:int,approved:bool=False) -> dict:
        """Save a new cover letter version. Generated drafts must use approved=false."""
        services.applications.get(application_id)
        return services.documents.save(document_type,title,application_id,text_content,approved=approved)
    @mcp.tool(name="documents.approve")
    def documents_approve(document_id:int) -> dict: """Approve one document version and supersede prior approval in its scope."""; return services.documents.approve(document_id)
    @mcp.resource("jobtron://documents/{document_id}",mime_type="application/octet-stream")
    def document_resource(document_id:int) -> bytes: """Read exact uploaded document bytes, or UTF-8 for a text-only document."""; return services.documents.content(document_id)

    @mcp.tool(name="assets.list")
    def assets_list(application_id:int|None=None) -> list[dict]: """List uploaded assets; content is available through jobtron://assets/{id}."""; return services.assets.list(application_id)
    @mcp.tool(name="assets.get")
    def assets_get(asset_id:int) -> dict: """Return asset metadata and its MCP resource URI."""; asset=services.assets.get(asset_id); return {**asset,"resource_uri":f"jobtron://assets/{asset_id}"}
    @mcp.resource("jobtron://assets/{asset_id}",mime_type="application/octet-stream")
    def asset_resource(asset_id:int) -> bytes: """Read the exact bytes of an uploaded image, PDF, or other asset."""; return services.assets.content(asset_id)

    return mcp


def run_stdio() -> None:
    settings = default_settings()
    session_file = None
    try:
        db = Database(settings.database_path)
        db.initialize()
        services = Services(db, FileStorage(settings.files_dir), settings.frontend_base_url)
        session_file = register_session(settings)

        def start_frontend() -> None:
            try:
                ensure_running(settings)
            except (PortConflictError, StartFailedError) as exc:
                sys.stderr.write(f"jobtron: web UI unavailable, MCP tools still work. {exc}\n")
                sys.stderr.flush()

        Thread(target=start_frontend, name="jobtron-ui-start", daemon=True).start()

        def ensure_frontend() -> None:
            ensure_running(settings)

        server = build_server(services, settings.frontend_base_url, ensure_frontend)

        async def serve() -> None:
            async with compatible_stdio_server() as (read_stream, write_stream):
                await server._lowlevel_server.run(  # pyright: ignore[reportPrivateUsage]
                    read_stream,
                    write_stream,
                    server._lowlevel_server.create_initialization_options(),  # pyright: ignore[reportPrivateUsage]
                )

        anyio.run(serve)
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        sys.stderr.flush()
        raise SystemExit(2) from exc
    finally:
        if session_file is not None:
            session_file.unlink(missing_ok=True)
