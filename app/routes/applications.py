from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app.services import NotFoundError
from app.services.timeutil import parse_stored, to_local
from .helpers import redirect, services, templates


def _activity_line(svc, application_id: int) -> str:
    """'Last activity N days ago · next: {event_type} on {date}' for the detail page."""
    now = datetime.now(UTC)
    last_activity_moment, _ = parse_stored(svc.applications.last_activity_at(application_id))
    days = (now - last_activity_moment).days if last_activity_moment else 0
    line = f"Last activity {days} day{'s' if days != 1 else ''} ago"
    next_event = svc.applications.next_event(application_id)
    if next_event:
        local_moment = to_local(next_event["starts_at_utc"])
        if local_moment:
            line += f" · next: {next_event['event_type']} on {local_moment.strftime('%b %d')}"
    return line

router=APIRouter(prefix="/applications")


def as_int(value):
    try: return int(value) if value not in (None,"") else None
    except (TypeError,ValueError): return None


@router.get("")
def application_list(request: Request, status: str|None=None, tag: str|None=None, q: str|None=None):
    svc=services(request)
    applications=svc.applications.list(status,tag,q)
    tag_counts:dict[str,int]={}
    for application in svc.applications.list():
        for t in (application["tags"] or "").split(","):
            if t: tag_counts[t]=tag_counts.get(t,0)+1
    tags=sorted(tag_counts,key=lambda t:(-tag_counts[t],t))
    return templates(request).TemplateResponse(request,"applications/list.html",{"applications":applications,"statuses":svc.applications.STATUSES,"selected_status":status,"selected_tag":tag,"query":q or "","tags":tags,"tag_counts":tag_counts})


@router.post("")
async def create_application(request: Request):
    form=dict(await request.form()); app=services(request).applications.create(form.pop("company_name"),form.pop("formal_title"),**form); return redirect(f"/applications/{app['id']}")


@router.get("/{application_id}")
def workspace(application_id:int,request:Request):
    svc=services(request)
    try: context=svc.applications.get_context(application_id)
    except NotFoundError as exc: raise HTTPException(404,str(exc)) from exc
    return templates(request).TemplateResponse(request,"applications/detail.html",{"context":context,"statuses":svc.applications.STATUSES,"archive":svc.applications.get_archive(application_id),"question_bank":svc.questions.search_bank("",20),"autofill":svc.applications.autofill_options(application_id),"activity_line":_activity_line(svc,application_id)})


@router.post("/{application_id}/autofill-plan")
async def prepare_autofill(application_id:int,request:Request):
    raw=await request.form()
    plan=services(request).applications.prepare_autofill(
        application_id,
        list(raw.getlist("sources")),
        list(raw.getlist("targets")),
        str(raw.get("custom_request", "")),
    )
    return JSONResponse(plan,status_code=200 if plan["valid"] else 422)


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
async def update_application(application_id:int,request:Request):
    raw=await request.form(); form=dict(raw); form["questions_reviewed"]="questions_reviewed" in raw; form["cover_letter_required"]="cover_letter_required" in raw; services(request).applications.update(application_id,**form); return redirect(f"/applications/{application_id}")


@router.post("/{application_id}/questions/reviewed")
async def mark_questions_reviewed(application_id:int,request:Request):
    form=await request.form(); services(request).applications.set_questions_reviewed(application_id,"questions_reviewed" in form); return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/questions")
async def add_question(application_id:int,request:Request):
    f=await request.form(); services(request).questions.add(application_id,f["question"],f.get("field_type","text"),f.get("allowed_choices",""),"required" in f,as_int(f.get("character_limit")),as_int(f.get("sort_order")) or 0); return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/questions/{question_id}/answers")
async def save_answer(application_id:int,question_id:int,request:Request):
    f=await request.form(); services(request).questions.save_answer(question_id,f["content"],f.get("source_label","manual"),"approved" in f,"save_to_bank" in f,f.get("keywords","")); return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/questions/{question_id}/bank/{bank_entry_id}")
async def apply_bank_answer(application_id:int,question_id:int,bank_entry_id:int,request:Request):
    f=await request.form(); services(request).questions.apply_bank_answer(question_id,bank_entry_id,"approved" in f); return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/questions/{question_id}/bank")
async def apply_selected_bank_answer(application_id:int,question_id:int,request:Request):
    raw=await request.form()
    bank_entry_id=as_int(raw.get("bank_entry_id"))
    if bank_entry_id is None: raise HTTPException(422,"Choose a question-bank answer")
    services(request).questions.apply_bank_answer(question_id,bank_entry_id,"approved" in raw)
    return redirect(f"/applications/{application_id}#questions")


@router.post("/{application_id}/academic-documents/{document_id}/considered")
async def mark_academic_document_considered(application_id:int,document_id:int,request:Request):
    f=await request.form(); services(request).documents.mark_considered(application_id,document_id,f.get("note","Reviewed in web workspace")); return redirect(f"/applications/{application_id}")


@router.post("/{application_id}/documents")
async def save_document(application_id:int,request:Request):
    f=await request.form(); upload=f.get("file"); filename=getattr(upload,"filename",None); content=await upload.read() if filename else None
    services(request).documents.save(f["document_type"],f["title"],application_id,f.get("text_content",""),filename,content,"approved" in f); return redirect(f"/applications/{application_id}#documents")


@router.post("/{application_id}/documents/{document_id}/approve")
def approve_document(application_id:int,document_id:int,request:Request): services(request).documents.approve(document_id); return redirect(f"/applications/{application_id}#documents")


@router.get("/{application_id}/documents/{document_id}")
def get_document(application_id:int,document_id:int,request:Request):
    svc=services(request); document=svc.documents.get(document_id)
    if document["application_id"]!=application_id: raise HTTPException(404)
    if document["file_path"]: return FileResponse(svc.storage.path(document["file_path"]),filename=document["title"])
    return Response(document["text_content"],media_type="text/plain")


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
