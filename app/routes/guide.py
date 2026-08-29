from fastapi import APIRouter, Request

from .helpers import templates

router=APIRouter()


@router.get("/guide")
def guide(request: Request):
    return templates(request).TemplateResponse(request,"guide.html")
