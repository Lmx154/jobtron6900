from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from contextlib import ExitStack, asynccontextmanager
from dataclasses import replace
from hashlib import sha256
from threading import Thread
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

import anyio
import mcp_types as types
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.shared.message import SessionMessage

from app.config import InstanceInUseError, InstanceLease, Settings, default_settings
from app.db import Database
from app.mcp.ui import APPLICATIONS_UI_URI, build_applications_ui
from app.services import Services
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


def build_server(services: Services, frontend_base_url: str = "http://127.0.0.1:8765") -> MCPServer:
    frontend_base_url=frontend_base_url.rstrip("/")
    mcp=MCPServer("jobtron6900",instructions="""Jobtron manages this user's job applications.

ONE RULE - repeat this loop:
1. Call workflow.next.
2. Say its "say_to_user" text word for word, including the URL it contains.
3. Do what "you_must" says. If "then_call" names a tool, call it, then go to 1.
4. If "then_call" is null, stop and wait for the user.

Shortcuts: a pasted job link -> workflow.start_from_url. "Show my applications" -> ui.show_dashboard, then share its frontend_url. "What am I missing?" -> candidate.readiness, then ui.show_setup.

Never call another Jobtron tool unless "you_must" or "then_call" names it.
Never state a fact about the user that is not already stored in Jobtron; ask instead.
Never mark an application Applied until the user says they submitted it themselves.""")

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
            "autofill":services.applications.autofill_options(application_id),
            "question_count":len(context["questions"]),
            "document_count":len(context["documents"]),
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
        """Report whether the user has the master resume and basic profile information Jobtron needs.

        CALL WHEN: before generating any resume, cover letter, or answer; or when the user asks what is missing.
        DO NOT CALL WHEN: you only need to read or list existing applications.
        RETURNS: capability ("draft_only" | "partial" | "full"), the exact missing items, setup_url, and
        say_to_user. If capability is not "full", say say_to_user to the user word for word and stop.
        """
        return services.candidate.readiness()

    @mcp.tool(
        name="ui.show_setup",
        title="Show the Jobtron setup page",
        description="Show the user where to upload their master resume and fill in basic profile information, and return the exact URL. CALL WHEN: candidate.readiness or applications.progress reports capability is not \"full\". DO NOT CALL WHEN: setup is already complete. RETURNS: frontend_url, which must be shared with the user. Does not mutate state.",
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
    def applications_update(application_id:int,company_name:str,formal_title:str,normalized_role:str="",role_family:str="",url:str="",location:str="",job_description:str="",status:str="Draft",notes:str="",user_confirmed_submission:bool=False) -> dict:
        """Update an application. Preserve every field you are not deliberately changing.

        CALL WHEN: workflow.next told you to record posting details or a user-confirmed change.
        DO NOT CALL WHEN: you only want to change status; use applications.set_status.
        RETURNS: the updated application. Setting status='Applied' requires
        user_confirmed_submission=true and is refused otherwise.
        """
        if status=="Applied" and not user_confirmed_submission:
            return {
                "refused":True,
                "reason":"Applied means the user submitted this externally. Ask them first.",
                "say_to_user":"Have you submitted this application yourself? I will only mark it Applied once you confirm.",
                "application":services.applications.get(application_id),
            }
        return services.applications.update(application_id,company_name=company_name,formal_title=formal_title,normalized_role=normalized_role,role_family=role_family,url=url,location=location,job_description=job_description,status=status,notes=notes)

    @mcp.tool(name="applications.set_status")
    def applications_set_status(application_id:int,status:str,user_confirmed_submission:bool=False) -> dict:
        """Change only an application's status, leaving every other field untouched.

        CALL WHEN: the user tells you an application moved (heard back, interviewing, rejected).
        DO NOT CALL WHEN: you are changing anything other than status.
        RETURNS: the updated application. status='Applied' requires user_confirmed_submission=true.
        """
        if status=="Applied" and not user_confirmed_submission:
            return {
                "refused":True,
                "reason":"Applied means the user submitted this externally. Ask them first.",
                "say_to_user":"Have you submitted this application yourself? I will only mark it Applied once you confirm.",
                "application":services.applications.get(application_id),
            }
        return services.applications.set_status(application_id,status)

    @mcp.tool(name="questions.extract_from_posting",structured_output=True)
    def questions_extract_from_posting(application_id:int) -> dict[str,Any]:
        """Fetch this application's saved job-posting URL and list any application questions on it.

        CALL WHEN: workflow.next returns step_id "extract_questions".
        DO NOT CALL WHEN: the application has no URL, or questions are already recorded.
        RETURNS: status ("ok" | "blocked" | "empty" | "error"), detected_questions, and say_to_user.
        A status other than "ok" is a normal outcome, not a failure: relay say_to_user and let the
        user paste the questions or upload screenshots.
        """
        application=services.applications.get(application_id)
        url=(application["url"] or "").strip()
        if not url:
            services.applications.record_question_extraction(application_id,"error","No posting URL is saved.")
            return {"status":"error","detected_questions":[],"reason":"No posting URL is saved.",
                    "say_to_user":"There is no link saved for this application. Paste the application questions, or tell me there are none.",
                    "frontend_url":f"{frontend_base_url}/applications/{application_id}","must_share_url":True}
        result=fetch_posting(url)
        services.applications.record_question_extraction(application_id,result["status"],result["reason"])
        assets_url=f"{frontend_base_url}/applications/{application_id}#assets"
        if result["status"]=="ok" and result["detected_questions"]:
            say=f"I found {len(result['detected_questions'])} possible application question(s) on that page."
        elif result["status"]=="ok":
            say="I read the posting but found no application questions on it. Paste any you see, or tell me there are none."
        else:
            say=(f"I could not read the application questions from that page. {result['reason']} "
                 f"Paste them here, drop screenshots at {assets_url}, or tell me there are none.")
        return {**result,"say_to_user":say,"assets_url":assets_url,
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


def _data_store_id(settings: Settings) -> str:
    """The identifier /healthz reports, so two processes can tell whether a
    running frontend is serving the same database."""
    return sha256(str(settings.database_path.resolve()).encode()).hexdigest()[:12]


def _frontend_health(base_url: str, timeout: float = 0.4) -> dict[str, Any] | None:
    """Return the /healthz payload of a Jobtron already serving here, else None."""
    try:
        with urlopen(f"{base_url}/healthz", timeout=timeout) as response:  # noqa: S310 - loopback
            payload = json.load(response)
    except Exception:
        return None
    return payload if isinstance(payload, dict) and payload.get("service") == "jobtron6900" else None


def _free_port(host: str) -> int:
    with socket.socket() as probe:
        probe.bind((host if host not in {"0.0.0.0", "::"} else "127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_frontend(settings: Settings, services: Services) -> tuple[uvicorn.Server, Thread]:
    from app.main import create_app

    frontend_app = create_app(
        settings,
        services,
        include_mcp_http=False,
        enforce_single_instance=False,
    )
    frontend = uvicorn.Server(
        uvicorn.Config(
            frontend_app,
            host=settings.host,
            port=settings.port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = Thread(target=frontend.run, name="jobtron-frontend", daemon=True)
    thread.start()

    health_url = f"{settings.frontend_base_url}/healthz"
    deadline = time.monotonic() + 5
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if not thread.is_alive():
            break
        try:
            with urlopen(health_url, timeout=0.3) as response:  # noqa: S310 - loopback URL
                payload = json.load(response)
            if payload.get("service") == "jobtron6900" and payload.get("status") == "ready":
                return frontend, thread
        except Exception as exc:
            last_error = exc
            time.sleep(0.05)

    frontend.should_exit = True
    thread.join(timeout=2)
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(f"Jobtron frontend did not become ready at {health_url}{detail}")


def _resolve_frontend(
    settings: Settings, services: Services
) -> tuple[Settings, uvicorn.Server | None, Thread | None]:
    """Decide who serves the web UI.

    Every MCP client gets its own Jobtron process, but they all share one
    database, so only the first needs to host the frontend. A later process
    that finds a healthy Jobtron already serving this same database simply
    points at it. Serving MCP tools never depends on winning that race.
    """
    existing = _frontend_health(settings.frontend_base_url)
    if existing and existing.get("data_store") == _data_store_id(settings):
        return settings, None, None

    if existing is None:
        try:
            frontend, thread = _start_frontend(settings, services)
            return settings, frontend, thread
        except Exception:
            # Lost the race to another starter; share theirs if it is ours.
            recheck = _frontend_health(settings.frontend_base_url)
            if recheck and recheck.get("data_store") == _data_store_id(settings):
                return settings, None, None

    # The port is taken by something that is not our Jobtron. Use another one
    # rather than refusing to start: the URLs we hand the user must be live.
    fallback = replace(settings, port=_free_port(settings.host))
    services.base_url = fallback.frontend_base_url.rstrip("/")
    frontend, thread = _start_frontend(fallback, services)
    return fallback, frontend, thread


def run_stdio() -> None:
    settings = default_settings()
    try:
        db = Database(settings.database_path)
        db.initialize()
        services = Services(db, FileStorage(settings.files_dir), settings.frontend_base_url)

        with ExitStack() as stack:
            # Advisory only: it records which process owns the frontend, and is
            # no longer allowed to stop a second client from serving its tools.
            try:
                stack.enter_context(InstanceLease(settings))
            except InstanceInUseError:
                pass

            settings, frontend, frontend_thread = _resolve_frontend(settings, services)
            services.base_url = settings.frontend_base_url.rstrip("/")
            server = build_server(services, settings.frontend_base_url)

            async def serve() -> None:
                async with compatible_stdio_server() as (read_stream, write_stream):
                    await server._lowlevel_server.run(  # pyright: ignore[reportPrivateUsage]
                        read_stream,
                        write_stream,
                        server._lowlevel_server.create_initialization_options(),  # pyright: ignore[reportPrivateUsage]
                    )

            try:
                anyio.run(serve)
            finally:
                if frontend is not None and frontend_thread is not None:
                    frontend.should_exit = True
                    frontend_thread.join(timeout=2)
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        sys.stderr.flush()
        raise SystemExit(2) from exc
