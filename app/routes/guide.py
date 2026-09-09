from fastapi import APIRouter, Request

from .helpers import templates

router=APIRouter()


@router.get("/guide")
def guide(request: Request):
    settings = request.app.state.settings
    return templates(request).TemplateResponse(
        request,
        "guide.html",
        {
            "dashboard_url": f"{settings.frontend_base_url}/applications",
            "data_dir": settings.data_dir,
        },
    )
