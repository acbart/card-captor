"""Activity and card views."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Activity, Card, SourceImage, Student, Submission
from ...services import activity as activity_service
from ...services import audit as audit_service
from ...services import review as review_service
from ..app import get_db, templates
from ..urls import redirect_home, redirect_to_activity

router = APIRouter(tags=["activities"])


def _get_activity(db: Session, activity_id: int) -> Activity:
    activity = db.get(Activity, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


@router.post("/courses")
def create_course(
    name: str = Form(...),
    term: str = Form(""),
    canvas_course_id: str = Form(""),
    email_domain: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    activity_service.create_course(
        db,
        name=name,
        term=term,
        canvas_course_id=canvas_course_id or None,
        email_domain=email_domain or None,
    )
    return redirect_home()


@router.post("/activities")
def create_activity(
    course_id: int = Form(...),
    name: str = Form(...),
    date: str = Form(""),
    point_value: float = Form(1.0),
    answers: str = Form(""),
    rule_type: str = Form("case_insensitive"),
    canvas_assignment_id: str = Form(""),
    canvas_assignment_mode: str = Form("direct"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    accepted = [a.strip() for a in answers.split(",") if a.strip()]
    activity = activity_service.create_activity(
        db,
        course_id=course_id,
        name=name,
        date=date or None,
        point_value=point_value,
        expected_answers=accepted,
        grading_rules={"rule_type": rule_type, "accepted_answers": accepted},
        canvas_assignment_id=canvas_assignment_id or None,
        canvas_assignment_mode=canvas_assignment_mode or None,
    )
    return redirect_to_activity(activity.id)


@router.get("/activities/{activity_id}", response_class=HTMLResponse)
def activity_detail(
    activity_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    activity = _get_activity(db, activity_id)
    summary = activity_service.activity_status(db, activity_id)
    rules = activity_service.grading_rules_for(activity)
    submissions = review_service.build_review_queue(activity_id, db)
    return templates.TemplateResponse(
        request,
        "activity.html",
        {
            "activity": activity,
            "summary": summary,
            "rules": rules,
            "submissions": submissions,
            "colors": review_service.color_breakdown(activity_id, db),
            "log": audit_service.get_entity_log(db, "activity", activity_id, 20),
        },
    )


@router.get("/activities/{activity_id}/cards", response_class=HTMLResponse)
def card_review(
    activity_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    activity = _get_activity(db, activity_id)
    images = list(
        db.scalars(
            select(SourceImage)
            .where(SourceImage.activity_id == activity_id)
            .order_by(SourceImage.id)
        )
    )
    cards_by_image: dict[int, list[Card]] = {}
    for image in images:
        cards_by_image[image.id] = list(
            db.scalars(select(Card).where(Card.source_image_id == image.id).order_by(Card.id))
        )
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "activity": activity,
            "mode": "cards",
            "images": images,
            "cards_by_image": cards_by_image,
            "submissions": [],
            "filter": "all",
            "students": [],
        },
    )


@router.get("/activities/{activity_id}/finalize", response_class=HTMLResponse)
def finalize_screen(
    activity_id: int, request: Request, message: str = "", db: Session = Depends(get_db)
) -> HTMLResponse:
    activity = _get_activity(db, activity_id)
    summary = activity_service.activity_status(db, activity_id)
    outstanding = review_service.build_review_queue(
        activity_id, db, {"filter": "needs_review"}
    )
    return templates.TemplateResponse(
        request,
        "finalize.html",
        {
            "activity": activity,
            "summary": summary,
            "outstanding": outstanding,
            "canvas_configured": bool(activity.canvas_assignment_id),
            "message": message,
        },
    )


@router.post("/activities/{activity_id}/finalize")
def finalize(activity_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    activity = _get_activity(db, activity_id)
    activity_service.finalize_activity(db, activity)
    return redirect_to_activity(activity_id, "finalize")


@router.post("/activities/{activity_id}/process")
def process(
    activity_id: int, force: bool = Form(False), db: Session = Depends(get_db)
) -> RedirectResponse:
    from ...services import processing as processing_service

    activity = _get_activity(db, activity_id)
    processing_service.process_activity(db, activity, force=force)
    return redirect_to_activity(activity_id)
