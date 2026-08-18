"""Review queue and instructor corrections."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Activity, Student, Submission
from ...services import audit as audit_service
from ...services import review as review_service
from ..app import get_db, templates
from ..urls import redirect_to_activity

router = APIRouter(tags=["review"])


def _get_activity(db: Session, activity_id: int) -> Activity:
    activity = db.get(Activity, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


@router.get("/activities/{activity_id}/review", response_class=HTMLResponse)
def review_queue(
    activity_id: int,
    request: Request,
    filter: str = "needs_review",
    color: Optional[str] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    activity = _get_activity(db, activity_id)
    submissions = review_service.build_review_queue(
        activity_id, db, {"filter": filter, "color": color}
    )
    students = list(
        db.scalars(
            select(Student).where(Student.course_id == activity.course_id).order_by(Student.name)
        )
    )
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "activity": activity,
            "mode": "submissions",
            "submissions": submissions,
            "students": students,
            "filter": filter,
            "colors": review_service.color_breakdown(activity_id, db),
            "images": [],
            "cards_by_image": {},
            "flags_from_json": review_service.flags_from_json,
        },
    )


@router.get("/activities/{activity_id}/submissions/{sub_id}", response_class=HTMLResponse)
def card_detail(
    activity_id: int, sub_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    activity = _get_activity(db, activity_id)
    submission = db.get(Submission, sub_id)
    if submission is None or submission.activity_id != activity_id:
        raise HTTPException(status_code=404, detail="Submission not found")

    students = list(
        db.scalars(
            select(Student).where(Student.course_id == activity.course_id).order_by(Student.name)
        )
    )

    def _candidates(raw: Optional[str]) -> list:
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return data if isinstance(data, list) else []

    return templates.TemplateResponse(
        request,
        "card_detail.html",
        {
            "activity": activity,
            "submission": submission,
            "students": students,
            "flags": review_service.flags_from_json(submission.review_flags),
            "name_candidates": _candidates(submission.name_candidates),
            "email_candidates": _candidates(submission.email_candidates),
            "answer_candidates": _candidates(submission.answer_candidates),
            "log": audit_service.get_entity_log(db, "submission", sub_id, 25),
        },
    )


@router.post("/activities/{activity_id}/submissions/{sub_id}")
def save_correction(
    activity_id: int,
    sub_id: int,
    name: str = Form(""),
    email: str = Form(""),
    answer: str = Form(""),
    student_id: str = Form(""),
    score: str = Form(""),
    mark_reviewed: bool = Form(False),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    submission = db.get(Submission, sub_id)
    if submission is None or submission.activity_id != activity_id:
        raise HTTPException(status_code=404, detail="Submission not found")

    parsed_student: Optional[int]
    try:
        parsed_student = int(student_id) if student_id.strip() else None
    except ValueError:
        parsed_student = None

    parsed_score: Optional[float]
    try:
        parsed_score = float(score) if score.strip() else None
    except ValueError:
        parsed_score = None

    review_service.apply_correction(
        db,
        submission,
        name=name if name.strip() else None,
        email=email if email.strip() else None,
        answer=answer if answer.strip() else None,
        student_id=parsed_student,
        score=parsed_score,
        clear_score=not score.strip(),
        mark_reviewed=mark_reviewed,
    )
    return redirect_to_activity(activity_id, "review")
