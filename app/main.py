from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings, default_settings
from app.db import Database
from app.mcp import build_server
from app.routes import applications_router, dashboard_router, guide_router, profile_router
from app.services import Services
from app.storage import FileStorage


def create_app(settings: Settings|None=None) -> FastAPI:
    settings=settings or default_settings(); db=Database(settings.database_path); db.initialize(); services=Services(db,FileStorage(settings.files_dir)); mcp=build_server(services,settings.frontend_base_url)
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with mcp.session_manager.run(): yield
    app=FastAPI(title="Job Application Workstation",lifespan=lifespan)
    app.state.services=services; app.state.templates=Jinja2Templates(directory=Path(__file__).parent/"templates"); app.state.mcp=mcp
    app.mount("/static",StaticFiles(directory=Path(__file__).parent/"static"),name="static")
    app.include_router(dashboard_router); app.include_router(profile_router); app.include_router(applications_router); app.include_router(guide_router)
    app.mount("/mcp",mcp.streamable_http_app(streamable_http_path="/",json_response=True))
    return app


app=create_app()


def run() -> None:
    settings=default_settings(); uvicorn.run("app.main:app",host=settings.host,port=settings.port,reload=False)
