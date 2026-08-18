"""Canvas upload endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ...db.models import Activity
from ...services import canvas as canvas_service
from ..app import get_db
from ..urls import redirect_to_activity

router = APIRouter(tags=["canvas"])


@router.post("/activities/{activity_id}/canvas-upload")
async def canvas_upload(
    activity_id: int,
    dry_run: bool = Form(True),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    activity = db.get(Activity, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")

    try:
        result, warnings = await canvas_service.upload_activity_grades(
            db, activity, dry_run=dry_run
        )
    except ValueError as exc:
        return redirect_to_activity(activity_id, "finalize", str(exc))

    prefix = "[dry run] " if result.dry_run else ""
    if result.skipped:
        message = f"{prefix}Payload unchanged - upload skipped"
    else:
        message = (
            f"{prefix}{result.success_count} grade(s) ok, {result.error_count} error(s)"
        )
    if warnings:
        message += f"; {len(warnings)} warning(s)"

    return redirect_to_activity(activity_id, "finalize", message)
