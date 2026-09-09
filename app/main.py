from __future__ import annotations

from contextlib import asynccontextmanager, nullcontext
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import InstanceLease, Settings, default_settings
from app.db import Database
from app.mcp import build_server
from app.routes import applications_router, dashboard_router, guide_router, profile_router
from app.services import Services
from app.storage import FileStorage


from hashlib import sha256

def create_app(
    settings: Settings | None = None,
    services: Services | None = None,
    *,
    include_mcp_http: bool = True,
    enforce_single_instance: bool = False,
) -> FastAPI:
    settings = settings or default_settings()
    if services is None:
        db = Database(settings.database_path)
        db.initialize()
        services = Services(db, FileStorage(settings.files_dir), settings.frontend_base_url)
    mcp = build_server(services, settings.frontend_base_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        with InstanceLease(settings) if enforce_single_instance else nullcontext():
            if include_mcp_http:
                async with mcp.session_manager.run():
                    yield
            else:
                yield

    app = FastAPI(title="Job Application Workstation", lifespan=lifespan)
    app.state.services = services
    app.state.settings = settings
    app.state.templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    app.state.mcp = mcp
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.include_router(dashboard_router)
    app.include_router(profile_router)
    app.include_router(applications_router)
    app.include_router(guide_router)

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict:
        database_path = str(settings.database_path.resolve())
        return {
            "service": "jobtron6900",
            "status": "ready",
            "version": "0.1.0",
            "client": settings.client_name,
            "frontend_url": settings.frontend_base_url,
            "data_store": sha256(database_path.encode()).hexdigest()[:12],
        }

    if include_mcp_http:
        app.mount("/mcp", mcp.streamable_http_app(streamable_http_path="/", json_response=True))
    return app


def __getattr__(name: str):
    """Build the ASGI app only when something actually asks for it.

    Creating it at import time opened the user's real data store — and now runs
    migrations against it — as a side effect of merely importing this module,
    which the test suite does. Deferring it keeps `import app.main` inert.
    """
    if name == "app":
        return create_app(enforce_single_instance=True)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def run() -> None:
    settings = default_settings()
    uvicorn.run(create_app(enforce_single_instance=True), host=settings.host, port=settings.port, reload=False)
