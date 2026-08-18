"""Export endpoints."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_config
from ...db.models import Activity, Student, Submission
from ...services import export as export_service
from ..app import get_db
from ..urls import redirect_to_activity

router = APIRouter(tags=["export"])


def _get_activity(db: Session, activity_id: int) -> Activity:
    activity = db.get(Activity, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


@router.post("/activities/{activity_id}/export")
def trigger_export(
    activity_id: int, subfolder: str = Form(""), db: Session = Depends(get_db)
) -> RedirectResponse:
    _get_activity(db, activity_id)
    config = get_config()
    target = config.exports_dir
    name = re.sub(r"[^\w\-]+", "_", subfolder.strip()).strip("_")
    if name:
        target = target / name[:100]
    export_service.export_both(activity_id, db, target)
    return redirect_to_activity(activity_id, "finalize")


@router.get("/activities/{activity_id}/export/{kind}.csv", response_class=PlainTextResponse)
def download_csv(
    activity_id: int, kind: str, db: Session = Depends(get_db)
) -> PlainTextResponse:
    activity = _get_activity(db, activity_id)
    submissions = list(
        db.scalars(
            select(Submission)
            .where(Submission.activity_id == activity_id)
            .order_by(Submission.id)
        )
    )
    students = list(
        db.scalars(select(Student).where(Student.course_id == activity.course_id))
    )

    if kind == "canvas":
        content = export_service.export_canvas_csv(activity, submissions, students)
    elif kind == "audit":
        content = export_service.export_audit_csv(activity, submissions, students)
    elif kind == "flagged":
        content = export_service.export_flagged_csv(activity, submissions)
    else:
        raise HTTPException(status_code=404, detail="Unknown export type")

    return PlainTextResponse(
        content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="activity_{activity_id}_{kind}.csv"'
        },
    )
