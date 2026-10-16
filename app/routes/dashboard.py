from fastapi import APIRouter, Request

from .helpers import redirect

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    return redirect("/applications")
