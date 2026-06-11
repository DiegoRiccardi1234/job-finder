import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.container import AppContainer
from app.routers import chat as chat_router
from app.routers import jobs as jobs_router
from app.routers import preferences as preferences_router
from app.routers import profile as profile_router
from app.routers import providers as providers_router
from app.routers import scan as scan_router
from app.routers import scheduler as scheduler_router
from app.routers import system as system_router
from app.version import __version__

# Re-exported for backwards compatibility (tests / scripts import AppContainer
# from app.main); the implementation now lives in app.container.
__all__ = ["AppContainer", "app", "create_app"]


def create_app(workspace_dir: Path) -> FastAPI:
    container = AppContainer(workspace_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        container.autoscan.start()
        yield
        container.shutdown()

    fastapi_app = FastAPI(title="Job Finder", version=__version__, lifespan=lifespan)
    web_dir = (
        Path(sys._MEIPASS) / "web"  # type: ignore[attr-defined]
        if getattr(sys, "frozen", False)
        else workspace_dir / "web"
    )
    fastapi_app.mount("/web", StaticFiles(directory=web_dir), name="web")

    @fastapi_app.get("/")
    def home() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @fastapi_app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(web_dir / "favicon.svg", media_type="image/svg+xml")

    for module in (
        system_router,
        providers_router,
        profile_router,
        scan_router,
        jobs_router,
        chat_router,
        preferences_router,
        scheduler_router,
    ):
        fastapi_app.include_router(module.build_router(container))

    return fastapi_app


_DEFAULT_WORKSPACE = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = (
    Path(os.environ["JOBFINDER_WORKSPACE"]).resolve()
    if os.environ.get("JOBFINDER_WORKSPACE")
    else _DEFAULT_WORKSPACE
)
app = create_app(WORKSPACE_DIR)
