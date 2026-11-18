from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.routes_admin import router as admin_router
from app.api.routes_editions import router as editions_router
from app.api.routes_rankings import router as rankings_router
from app.api.routes_repos import router as repos_router
from app.core.config import get_settings
from app.db.archive import restore_history
from app.db.session import SessionLocal, init_db
from app.web.routes import router as web_router


def create_app(archive_dir: Path | None = None, restore_on_startup: bool = True) -> FastAPI:
    settings = get_settings()
    public_dir = archive_dir if archive_dir is not None else settings.archive_dir

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if restore_on_startup:
            init_db()
            with SessionLocal() as db:
                restore_history(db, settings.state_dir, public_dir)
        yield

    app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
    app.state.archive_dir = public_dir
    app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "http_error", "message": str(exc.detail), "details": {}}},
        )

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "app": settings.app_name, "env": settings.env}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    app.include_router(editions_router)
    app.include_router(rankings_router)
    app.include_router(repos_router)
    app.include_router(admin_router)
    app.include_router(web_router)
    return app


app = create_app()
