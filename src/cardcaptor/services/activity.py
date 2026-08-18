"""Course and activity CRUD."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Activity, Card, Course, SourceImage, Student, Submission
from . import audit
from .grading import GradingRules, parse_grading_rules_from_dict


def create_course(
    session: Session,
    name: str,
    term: str = "",
    canvas_course_id: Optional[str] = None,
    email_domain: Optional[str] = None,
) -> Course:
    course = Course(
        name=name,
        term=term or "",
        canvas_course_id=canvas_course_id or None,
        email_domain=email_domain or None,
    )
    session.add(course)
    session.flush()
    audit.log_action(session, "course", course.id, "create_course", "instructor", {"name": name})
    session.commit()
    return course


def list_courses(session: Session) -> list[Course]:
    return list(session.scalars(select(Course).order_by(Course.id)))


def get_course(session: Session, course_id: int) -> Optional[Course]:
    return session.get(Course, course_id)


def parse_date(value: Any) -> Optional[dt.date]:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {value!r}")


def create_activity(
    session: Session,
    course_id: int,
    name: str,
    date: Any = None,
    point_value: float = 1.0,
    expected_answers: Optional[list[str]] = None,
    grading_rules: Optional[dict] = None,
    canvas_assignment_id: Optional[str] = None,
    canvas_assignment_mode: Optional[str] = None,
) -> Activity:
    answers = [str(a) for a in (expected_answers or [])]
    rules = dict(grading_rules or {})
    rules.setdefault("rule_type", "mcq" if _looks_like_mcq(answers) else "case_insensitive")
    rules.setdefault("accepted_answers", answers)
    rules.setdefault("point_value", float(point_value))

    activity = Activity(
        course_id=course_id,
        name=name,
        date=parse_date(date),
        point_value=float(point_value),
        expected_answers=json.dumps(answers),
        grading_rules=json.dumps(rules),
        canvas_assignment_id=canvas_assignment_id or None,
        canvas_assignment_mode=canvas_assignment_mode or None,
    )
    session.add(activity)
    session.flush()
    audit.log_action(
        session,
        "activity",
        activity.id,
        "create_activity",
        "instructor",
        {"name": name, "course_id": course_id, "rules": rules},
    )
    session.commit()
    return activity


def _looks_like_mcq(answers: list[str]) -> bool:
    return bool(answers) and all(len(a.strip()) == 1 and a.strip().isalpha() for a in answers)


def list_activities(session: Session, course_id: Optional[int] = None) -> list[Activity]:
    stmt = select(Activity).order_by(Activity.id)
    if course_id is not None:
        stmt = stmt.where(Activity.course_id == course_id)
    return list(session.scalars(stmt))


def get_activity(session: Session, activity_id: int) -> Optional[Activity]:
    return session.get(Activity, activity_id)


def get_activity_by_name(session: Session, name: str) -> Optional[Activity]:
    return session.scalars(select(Activity).where(Activity.name == name)).first()


def resolve_activity(
    session: Session, activity_id: Optional[int] = None, activity_name: Optional[str] = None
) -> Activity:
    """Look up an activity by id or name, raising a helpful error if missing."""
    activity: Optional[Activity] = None
    if activity_id is not None:
        activity = get_activity(session, activity_id)
    elif activity_name:
        activity = get_activity_by_name(session, activity_name)
    if activity is None:
        raise LookupError("Activity not found (specify --activity-id or --activity-name)")
    return activity


def update_activity(session: Session, activity: Activity, **fields: Any) -> Activity:
    changed: dict[str, Any] = {}
    for key, value in fields.items():
        if not hasattr(activity, key) or value is None:
            continue
        if key == "date":
            value = parse_date(value)
        if key == "expected_answers" and isinstance(value, list):
            value = json.dumps([str(v) for v in value])
        if key == "grading_rules" and isinstance(value, dict):
            value = json.dumps(value)
        if getattr(activity, key) != value:
            setattr(activity, key, value)
            changed[key] = str(value)
    if changed:
        audit.log_action(
            session, "activity", activity.id, "update_activity", "instructor", changed
        )
        session.commit()
    return activity


def grading_rules_for(activity: Activity) -> GradingRules:
    try:
        raw = json.loads(activity.grading_rules or "{}")
    except (TypeError, ValueError):
        raw = {}
    rules = parse_grading_rules_from_dict(raw if isinstance(raw, dict) else {})
    if not rules.accepted_answers:
        try:
            rules.accepted_answers = [str(a) for a in json.loads(activity.expected_answers or "[]")]
        except (TypeError, ValueError):
            rules.accepted_answers = []
    rules.point_value = float(activity.point_value or rules.point_value)
    return rules


def activity_status(session: Session, activity_id: int) -> dict[str, Any]:
    """Summary counters used by the CLI ``status`` command and the dashboard."""
    activity = get_activity(session, activity_id)
    if activity is None:
        raise LookupError(f"No activity with id {activity_id}")

    image_count = session.scalar(
        select(func.count(SourceImage.id)).where(SourceImage.activity_id == activity_id)
    ) or 0
    card_count = session.scalar(
        select(func.count(Card.id))
        .join(SourceImage, Card.source_image_id == SourceImage.id)
        .where(SourceImage.activity_id == activity_id)
    ) or 0
    submissions = list(
        session.scalars(select(Submission).where(Submission.activity_id == activity_id))
    )
    roster_size = session.scalar(
        select(func.count(Student.id)).where(Student.course_id == activity.course_id)
    ) or 0

    needs_review = sum(1 for s in submissions if s.needs_review)
    matched = sum(1 for s in submissions if s.effective_student_id)
    graded = sum(
        1
        for s in submissions
        if s.instructor_score is not None
        or s.final_score is not None
        or s.auto_score is not None
    )
    total_score = sum(s.effective_score for s in submissions)

    return {
        "activity_id": activity.id,
        "activity_name": activity.name,
        "course_id": activity.course_id,
        "date": activity.date.isoformat() if activity.date else None,
        "point_value": activity.point_value,
        "roster_size": roster_size,
        "source_images": image_count,
        "cards_detected": card_count,
        "submissions": len(submissions),
        "needs_review": needs_review,
        "reviewed": len(submissions) - needs_review,
        "matched_students": matched,
        "unmatched": len(submissions) - matched,
        "graded": graded,
        "mean_score": round(total_score / len(submissions), 3) if submissions else 0.0,
        "finalized_at": activity.finalized_at.isoformat() if activity.finalized_at else None,
        "canvas_uploaded_at": (
            activity.canvas_uploaded_at.isoformat() if activity.canvas_uploaded_at else None
        ),
    }


def finalize_activity(session: Session, activity: Activity) -> Activity:
    from ..db.models import utcnow

    activity.finalized_at = utcnow()
    audit.log_action(session, "activity", activity.id, "finalize", "instructor", {})
    session.commit()
    return activity
