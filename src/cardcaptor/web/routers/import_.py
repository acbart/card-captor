"""Import screens and handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ...db.models import Activity
from ...services import import_ as import_service
from ...services import roster as roster_service
from ..app import get_db, templates
from ..urls import redirect_to_activity

router = APIRouter(tags=["import"])


def _get_activity(db: Session, activity_id: int) -> Activity:
    activity = db.get(Activity, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


@router.get("/activities/{activity_id}/import", response_class=HTMLResponse)
def import_screen(
    activity_id: int,
    request: Request,
    message: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    activity = _get_activity(db, activity_id)
    images = import_service.list_source_images(db, activity_id)
    return templates.TemplateResponse(
        request,
        "import.html",
        {"activity": activity, "images": images, "message": message},
    )


@router.post("/activities/{activity_id}/import")
async def handle_import(
    activity_id: int,
    folder: str = Form(""),
    url: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    activity = _get_activity(db, activity_id)
    messages: list[str] = []

    uploads: list[tuple[str, bytes]] = []
    for upload in files or []:
        if not upload.filename:
            continue
        payload = await upload.read()
        if payload:
            uploads.append((upload.filename, payload))

    if uploads:
        result = import_service.import_uploaded_files(db, activity.id, uploads)
        messages.append(
            f"{result.count} uploaded file(s) imported, "
            f"{result.skipped_duplicates} duplicate(s) skipped"
        )
        messages.extend(result.rejected)

    if folder.strip():
        try:
            result = import_service.import_from_folder(db, activity.id, Path(folder.strip()))
            messages.append(f"{result.count} image(s) imported from folder")
        except (OSError, ValueError) as exc:
            messages.append(f"Folder import failed: {exc}")

    if url.strip():
        try:
            result = import_service.import_from_url(db, activity.id, url.strip())
            messages.append(f"{result.count} image(s) imported from URL")
        except Exception as exc:  # network/validation errors
            messages.append(f"URL import failed: {exc}")

    if not messages:
        messages.append("Nothing to import")

    return redirect_to_activity(activity_id, "import", "; ".join(messages))


@router.post("/activities/{activity_id}/roster")
async def import_roster(
    activity_id: int,
    roster_file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    from ...config import get_config

    activity = _get_activity(db, activity_id)
    config = get_config()
    config.ensure_dirs()
    payload = await roster_file.read()
    safe_name = import_service.sanitize_filename(roster_file.filename or "roster.csv")
    staged = config.tmp_dir / f"roster_{activity_id}_{safe_name}"
    staged.write_bytes(payload)
    try:
        count, warnings = roster_service.import_roster_from_csv(
            staged, activity.course_id, db
        )
    finally:
        staged.unlink(missing_ok=True)

    message = f"Imported {count} students"
    if warnings:
        message += f" ({len(warnings)} warning(s))"
    return redirect_to_activity(activity_id, "import", message)
