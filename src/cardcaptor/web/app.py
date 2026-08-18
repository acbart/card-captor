"""FastAPI application. Local-only, no authentication, no telemetry."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.session import get_session, init_db
from ..services import activity as activity_service
from ..services import audit as audit_service

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_db() -> Iterator[Session]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def create_app() -> FastAPI:
    init_db()
    application = FastAPI(
        title="card-captor",
        description="Local-only index card quiz processing",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    from .routers import activities, canvas, export, import_, review

    application.include_router(activities.router)
    application.include_router(import_.router)
    application.include_router(review.router)
    application.include_router(export.router)
    application.include_router(canvas.router)

    @application.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
        courses = activity_service.list_courses(db)
        activities_list = activity_service.list_activities(db)
        summaries = [activity_service.activity_status(db, a.id) for a in activities_list]
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "courses": courses,
                "activities": activities_list,
                "summaries": summaries,
                "recent_log": audit_service.recent_log(db, 15),
            },
        )

    @application.get("/media/{kind}/{path:path}")
    def media(kind: str, path: str) -> FileResponse:
        """Serve card crops and original photos from the data directory only."""
        config = get_config()
        roots = {"cards": config.cards_dir, "originals": config.originals_dir}
        root = roots.get(kind)
        if root is None:
            raise HTTPException(status_code=404, detail="Unknown media kind")
        target = (root / path).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(target)

    @application.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return application


app = create_app()


def main() -> None:  # pragma: no cover - console-script entry point
    import uvicorn

    config = get_config()
    uvicorn.run(app, host=config.web_host, port=config.web_port)


if __name__ == "__main__":  # pragma: no cover
    main()
