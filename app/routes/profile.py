from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.services.standard_questions import STANDARD_QUESTIONS

from .helpers import redirect, services, templates
from .applications import call_service

router=APIRouter(prefix="/profile")


def _standard_answers(svc):
    rows=[]
    for entry in STANDARD_QUESTIONS:
        bank_entry=svc.questions.get_bank_entry_by_question(entry["canonical_question"])
        rows.append({"key":entry["key"],"canonical_question":entry["canonical_question"],"answer":bank_entry["answer"] if bank_entry else ""})
    return rows


@router.get("")
def profile(request: Request):
    svc=services(request)
    return templates(request).TemplateResponse(request,"profile.html",{"profile":svc.candidate.get_profile(),"resumes":svc.documents.list(None,"master_resume"),"standard_questions":_standard_answers(svc)})


@router.post("")
async def update_profile(request: Request):
    form=await request.form(); services(request).candidate.update_profile(**dict(form)); return redirect("/profile")


@router.post("/standard-answers")
async def update_standard_answers(request: Request):
    svc=services(request); svc.questions.seed_defaults()
    form=await request.form()
    for entry in STANDARD_QUESTIONS:
        key = f"answer_{entry['key']}"
        if key not in form:
            continue
        content = str(form[key]).strip()
        bank_entry = svc.questions.get_bank_entry_by_question(entry["canonical_question"])
        if not content:
            if bank_entry:
                svc.questions.clear_bank_entry(bank_entry["id"])
            continue
        svc.questions.save_bank_entry(entry["canonical_question"],content,entry["keywords"],approved=True,bank_entry_id=bank_entry["id"] if bank_entry else None)
    return redirect("/profile#standard-answers")


@router.post("/facts")
async def add_fact(request: Request):
    form=await request.form(); services(request).candidate.add_fact(form["category"],form["title"],form["description"],form.get("tags",""),form.get("source",""),"verified" in form); return redirect("/profile")


@router.post("/facts/{fact_id}")
async def update_fact(fact_id:int,request:Request):
    form=await request.form(); values=dict(form); verified="verified" in values; values.pop("verified",None); services(request).candidate.update_fact(fact_id,**values,verified=verified); return redirect("/profile")


@router.post("/resume")
async def upload_resume(request: Request):
    form=await request.form(); upload=form.get("file")
    if not getattr(upload,"filename",None): raise HTTPException(422,"Choose a resume file")
    content=await upload.read()
    if not content: raise HTTPException(422,"Resume file is empty")
    call_service(services(request).documents.save,"master_resume",form.get("title") or upload.filename,filename=upload.filename,content=content,text_content=form.get("text_content",""),approved="approved" in form)
    return redirect("/profile")


@router.post("/academic-documents")
async def upload_academic_document(request: Request):
    form=await request.form(); upload=form["file"]; content=await upload.read(); document_type=form.get("document_type","academic_transcript")
    if document_type not in services(request).documents.ACADEMIC_TYPES: return Response("Unsupported academic document type",status_code=400)
    services(request).documents.save(document_type,form.get("title") or upload.filename,filename=upload.filename,content=content,text_content=form.get("text_content",""),approved="approved" in form,use_for_applications="use_for_applications" in form)
    return redirect("/profile")


@router.post("/academic-documents/{document_id}/application-use")
async def set_academic_document_use(document_id:int,request:Request):
    form=await request.form(); services(request).documents.set_application_use(document_id,"use_for_applications" in form); return redirect("/profile")


@router.get("/academic-documents/{document_id}")
def download_academic_document(document_id:int,request:Request):
    svc=services(request); document=call_service(svc.documents.get,document_id)
    if document["application_id"] is not None or document["document_type"] not in svc.documents.ACADEMIC_TYPES: return Response(status_code=404)
    if document["file_path"]: return FileResponse(svc.storage.path(document["file_path"]),filename=document["title"])
    return Response(document["text_content"],media_type="text/plain")


@router.get("/resume/{document_id}")
def download_resume(document_id:int,request:Request):
    svc=services(request); document=call_service(svc.documents.get,document_id)
    if document["application_id"] is not None or document["document_type"] != "master_resume": return Response(status_code=404)
    if document["file_path"]: return FileResponse(svc.storage.path(document["file_path"]),filename=document["title"])
    return Response(document["text_content"],media_type="text/plain")
