from fastapi import APIRouter, Request

from .helpers import services, templates

router=APIRouter()


@router.get("/")
def dashboard(request: Request):
    svc=services(request); applications=svc.applications.list(); events=svc.events.calendar()
    counts={status:sum(a["status"]==status for a in applications) for status in svc.applications.STATUSES}
    return templates(request).TemplateResponse(request,"dashboard.html",{"applications":applications[:8],"events":events[:20],"counts":counts})
