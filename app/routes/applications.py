from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app.services import NotFoundError
from .helpers import redirect, services, templates


def call_service(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def require_question(svc, application_id, question_id):
    call_service(svc.applications.get, application_id)
    if not any(q["id"] == question_id for q in svc.questions.list(application_id)):
        raise HTTPException(404, "Question not found in this application")


def require_document(svc, application_id, document_id):
    document = call_service(svc.documents.get, document_id)
    if document["application_id"] != application_id:
        raise HTTPException(404, "Document not found in this application")
    return document


router=APIRouter(prefix="/applications")


def as_int(value):
    try: return int(value) if value not in (None,"") else None
    except (TypeError,ValueError): return None


@router.get("")
def application_list(request: Request, active: str = "all", q: str | None = None):
    if active not in ("all", "active", "inactive"):
        raise HTTPException(422, "Choose all, active, or inactive")
    rows = services(request).applications.list(query=q, active=None if active == "all" else active == "active")
    return templates(request).TemplateResponse(request, "applications/list.html", {
        "applications": rows, "selected_active": active, "query": q or "",
    })


@router.post("")
async def create_application(request: Request):
    form = await request.form()
    company = str(form.get("company_name", "")).strip()
    title = str(form.get("formal_title", "")).strip()
    if not company or not title:
        raise HTTPException(422, "Company and job title are required")
    app = call_service(services(request).applications.create, company, title,
                       url=str(form.get("url", "")), job_description=str(form.get("job_description", "")))
    return redirect(f"/applications/{app['id']}")


@router.get("/{application_id}")
def workspace(application_id: int, request: Request):
    context = call_service(services(request).applications.get_context, application_id)
    letters = [doc for doc in context["documents"] if doc["document_type"] == "cover_letter"]
    return templates(request).TemplateResponse(request, "applications/detail.html", {
        "context": context, "cover_letters": letters, "cover_letter": letters[0] if letters else None,
        "legacy_documents": [doc for doc in context["documents"] if doc["document_type"] != "cover_letter"],
    })


@router.post("/{application_id}/active")
async def set_active(application_id: int, request: Request):
    form = await request.form()
    value = str(form.get("active", ""))
    if value not in ("true", "false"):
        raise HTTPException(422, "Active must be true or false")
    call_service(services(request).applications.set_active, application_id, value == "true")
    return redirect(f"/applications/{application_id}")


@router.post("/{application_id}/drafts")
def prepare_drafts(application_id: int, request: Request):
    plan = call_service(services(request).applications.prepare_drafts, application_id)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(plan, status_code=200 if plan["valid"] else 422)
    return templates(request).TemplateResponse(request, "applications/draft_request.html", {
        "plan": plan, "application_id": application_id,
    }, status_code=200 if plan["valid"] else 422)


@router.post("/{application_id}/bundles")
def create_bundle(application_id: int, request: Request):
    call_service(services(request).applications.create_bundle, application_id)
    return redirect(f"/applications/{application_id}#bundles")


@router.get("/{application_id}/bundles/{bundle_id}")
def download_bundle(application_id: int, bundle_id: int, request: Request):
    svc = services(request)
    content = call_service(svc.applications.bundle_zip, application_id, bundle_id)
    bundle = next(row for row in svc.applications.list_bundles(application_id) if row["id"] == bundle_id)
    stamp = bundle["created_at"][:19].replace("-", "").replace(":", "").replace(" ", "T")
    return Response(content, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="application-{application_id}-{stamp}Z-bundle-{bundle_id}.zip"',
    })


@router.post("/{application_id}/status")
async def set_status(application_id:int,request:Request):
    form=await request.form(); status=str(form.get("status",""))
    try: services(request).applications.set_status(application_id,status)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    except NotFoundError as exc: raise HTTPException(404,str(exc)) from exc
    next_path=str(form.get("next","") or "")
    safe_next=next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    return redirect(safe_next)


@router.post("/{application_id}")
async def update_application(application_id: int, request: Request):
    raw = await request.form()
    fields = {key: str(raw[key]) for key in ("company_name", "formal_title", "url", "location", "job_description", "notes") if key in raw}
    for key in ("company_name", "formal_title"):
        if key in fields and not fields[key].strip():
            raise HTTPException(422, "Company and job title are required")
    call_service(services(request).applications.update, application_id, **fields)
    return redirect(f"/applications/{application_id}")


@router.post("/{application_id}/questions/reviewed")
async def mark_questions_reviewed(application_id:int,request:Request):
    form=await request.form(); call_service(services(request).applications.set_questions_reviewed,application_id,"questions_reviewed" in form); return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/questions")
async def add_question(application_id:int,request:Request):
    f=await request.form(); call_service(services(request).applications.get,application_id)
    if not str(f.get("question", "")).strip(): raise HTTPException(422,"Question is required")
    call_service(services(request).questions.add,application_id,f["question"],f.get("field_type","text"),f.get("allowed_choices",""),"required" in f,as_int(f.get("character_limit")),as_int(f.get("sort_order")) or 0); return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/questions/{question_id}/answers")
async def save_answer(application_id:int,question_id:int,request:Request):
    f=await request.form(); require_question(services(request),application_id,question_id)
    if not str(f.get("content", "")).strip(): raise HTTPException(422,"Answer is required")
    call_service(services(request).questions.save_answer,question_id,f["content"],f.get("source_label","manual"),"approved" in f,"save_to_bank" in f,f.get("keywords","")); return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/answers")
async def save_all_answers(application_id:int,request:Request):
    """Save every edited answer on the page in one submit; untouched answers get no new version."""
    f=await request.form(); svc=services(request)
    context=call_service(svc.applications.get_context,application_id)
    for q in context["questions"]:
        raw=f.get(f"content-{q['id']}")
        if raw is None or not str(raw).strip(): continue
        content=str(raw).replace("\r\n","\n"); approved=f"approved-{q['id']}" in f
        latest=q["answers"][0] if q["answers"] else None
        if latest and content==latest["content"].replace("\r\n","\n"):
            if approved and not latest["approved"]: call_service(svc.questions.approve_answer,latest["id"])
            continue
        call_service(svc.questions.save_answer,q["id"],content,"manual",approved)
    return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/questions/{question_id}/bank/{bank_entry_id}")
async def apply_bank_answer(application_id:int,question_id:int,bank_entry_id:int,request:Request):
    f=await request.form(); require_question(services(request),application_id,question_id); call_service(services(request).questions.apply_bank_answer,question_id,bank_entry_id,"approved" in f); return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/questions/{question_id}/bank")
async def apply_selected_bank_answer(application_id:int,question_id:int,request:Request):
    raw=await request.form()
    bank_entry_id=as_int(raw.get("bank_entry_id"))
    if bank_entry_id is None: raise HTTPException(422,"Choose a question-bank answer")
    require_question(services(request),application_id,question_id)
    call_service(services(request).questions.apply_bank_answer,question_id,bank_entry_id,"approved" in raw)
    return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/academic-documents/{document_id}/considered")
async def mark_academic_document_considered(application_id:int,document_id:int,request:Request):
    f=await request.form(); services(request).documents.mark_considered(application_id,document_id,f.get("note","Reviewed in web workspace")); return redirect(f"/applications/{application_id}")


@router.post("/{application_id}/documents")
async def save_document(application_id: int, request: Request):
    form = await request.form()
    svc = services(request)
    call_service(svc.applications.get, application_id)
    if form.get("document_type") != "cover_letter":
        raise HTTPException(422, "Only cover letters are created in the tracker")
    content = str(form.get("text_content", "")).strip()
    if not content:
        raise HTTPException(422, "Cover letter text is required")
    call_service(svc.documents.save, "cover_letter", str(form.get("title") or "Cover letter"),
                 application_id, content, approved="approved" in form)
    return redirect(f"/applications/{application_id}#cover-letter")


@router.post("/{application_id}/documents/{document_id}/approve")
def approve_document(application_id: int, document_id: int, request: Request):
    svc = services(request)
    require_document(svc, application_id, document_id)
    call_service(svc.documents.approve, document_id)
    return redirect(f"/applications/{application_id}#cover-letter")


@router.get("/{application_id}/documents/{document_id}")
def get_document(application_id: int, document_id: int, request: Request):
    svc = services(request)
    document = require_document(svc, application_id, document_id)
    if document["file_path"]:
        return FileResponse(svc.storage.path(document["file_path"]), filename=document["title"])
    return Response(document["text_content"], media_type="text/plain")


@router.post("/{application_id}/assets")
async def save_asset(application_id:int,request:Request):
    f=await request.form(); upload=f["file"]; services(request).assets.save(f.get("title") or upload.filename,upload.filename,await upload.read(),application_id,f.get("asset_type","other"),f.get("notes","")); return redirect(f"/applications/{application_id}#assets")


@router.get("/{application_id}/assets/{asset_id}")
def get_asset(application_id:int,asset_id:int,request:Request):
    svc=services(request); asset=svc.assets.get(asset_id)
    if asset["application_id"]!=application_id: raise HTTPException(404)
    return FileResponse(svc.storage.path(asset["file_path"]),media_type=asset["mime_type"],filename=asset["title"])


@router.post("/{application_id}/events")
async def add_event(application_id:int,request:Request):
    f=await request.form(); services(request).events.add(application_id,f["event_type"],f["occurred_at"],f.get("notes","")); return redirect(f"/applications/{application_id}#events")


@router.post("/{application_id}/contacts")
async def add_contact(application_id:int,request:Request):
    f=await request.form(); services(request).applications.add_contact(application_id,f["name"],f.get("role",""),f.get("email",""),f.get("phone",""),f.get("notes","")); return redirect(f"/applications/{application_id}#events")
