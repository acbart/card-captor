"""Review queue: flag computation, filtering and instructor corrections."""

from __future__ import annotations

import json
from collections import Counter
from enum import Enum
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Activity, Card, Student, Submission, utcnow
from . import audit
from .roster import EMAIL_RE, is_valid_email, normalize_email

DEFAULT_THRESHOLDS: dict[str, float] = {
    "ocr_confidence": 0.6,
    "min_confidence_for_auto_review": 0.8,
    "match_confidence": 0.8,
    "color_confidence": 0.4,
}


class ReviewFlag(str, Enum):
    LOW_OCR_CONFIDENCE = "low_ocr_confidence"
    AMBIGUOUS_STUDENT = "ambiguous_student"
    MISSING_NAME = "missing_name"
    MISSING_EMAIL = "missing_email"
    INVALID_EMAIL = "invalid_email"
    UNREADABLE_ANSWER = "unreadable_answer"
    NO_ROSTER_MATCH = "no_roster_match"
    DUPLICATE_STUDENT = "duplicate_student"
    UNUSUAL_COLOR = "unusual_color"
    MULTIPLE_OCR_CANDIDATES = "multiple_ocr_candidates"
    CONFLICTING_IDENTITY = "conflicting_identity"


def _thresholds(overrides: Optional[dict[str, float]] = None) -> dict[str, float]:
    merged = dict(DEFAULT_THRESHOLDS)
    merged.update({k: float(v) for k, v in (overrides or {}).items() if v is not None})
    return merged


def _load_candidates(raw: Optional[str]) -> list[Any]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _majority_color(submissions: Iterable[Submission]) -> Optional[str]:
    counter = Counter(
        s.card.color_category
        for s in submissions
        if s.card is not None and s.card.color_category
    )
    if not counter:
        return None
    category, count = counter.most_common(1)[0]
    total = sum(counter.values())
    return category if total >= 3 and count / total >= 0.6 else None


def compute_review_flags(
    submission: Submission,
    all_submissions: list[Submission],
    activity: Optional[Activity] = None,
    thresholds: Optional[dict[str, float]] = None,
) -> list[ReviewFlag]:
    """Determine which review flags apply to a submission."""
    limits = _thresholds(thresholds)
    flags: list[ReviewFlag] = []

    # A field is suspect when it falls below either the "unreadable" floor or
    # the (usually stricter) bar required to accept a submission without review.
    ocr_limit = max(
        limits["ocr_confidence"], limits.get("min_confidence_for_auto_review", 0.0)
    )
    confidences = [
        submission.name_confidence,
        submission.email_confidence,
        submission.answer_confidence,
    ]
    if any(c is not None and c < ocr_limit for c in confidences):
        flags.append(ReviewFlag.LOW_OCR_CONFIDENCE)

    name = submission.effective_name.strip()
    email = submission.effective_email.strip()
    answer = submission.effective_answer.strip()

    if not name:
        flags.append(ReviewFlag.MISSING_NAME)
    if not email:
        flags.append(ReviewFlag.MISSING_EMAIL)
    else:
        domain = None
        if activity is not None and activity.course is not None:
            domain = activity.course.email_domain
        if not is_valid_email(email, domain):
            flags.append(ReviewFlag.INVALID_EMAIL)

    if not answer or (
        submission.answer_confidence is not None and submission.answer_confidence <= 0.0
    ):
        flags.append(ReviewFlag.UNREADABLE_ANSWER)

    student_id = submission.effective_student_id
    if student_id is None:
        flags.append(ReviewFlag.NO_ROSTER_MATCH)
    else:
        if (
            submission.match_confidence is not None
            and submission.match_confidence < limits["match_confidence"]
            and submission.instructor_student_id is None
        ):
            flags.append(ReviewFlag.AMBIGUOUS_STUDENT)
        duplicates = [
            s
            for s in all_submissions
            if s.id != submission.id and s.effective_student_id == student_id
        ]
        if duplicates:
            flags.append(ReviewFlag.DUPLICATE_STUDENT)

    if (submission.match_method or "") == "ambiguous":
        flags.append(ReviewFlag.AMBIGUOUS_STUDENT)

    for raw in (submission.name_candidates, submission.email_candidates, submission.answer_candidates):
        candidates = _load_candidates(raw)
        if len(candidates) > 1:
            flags.append(ReviewFlag.MULTIPLE_OCR_CANDIDATES)
            break

    card: Optional[Card] = submission.card
    if card is not None:
        color_limit = limits["color_confidence"]
        majority = _majority_color(all_submissions)
        if card.color_category in (None, "", "unknown"):
            flags.append(ReviewFlag.UNUSUAL_COLOR)
        elif card.color_confidence is not None and card.color_confidence < color_limit:
            flags.append(ReviewFlag.UNUSUAL_COLOR)
        elif majority and card.color_category != majority:
            flags.append(ReviewFlag.UNUSUAL_COLOR)

    if name and email and student_id is not None:
        # The name and email must point at the same roster entry.
        session = _session_of(submission)
        if session is not None:
            student = session.get(Student, student_id)
            if student is not None:
                email_matches = normalize_email(email) == normalize_email(student.email or "")
                name_similar = _name_similarity(name, student.name or "") >= 0.6
                if EMAIL_RE.match(normalize_email(email)) and not email_matches and not name_similar:
                    flags.append(ReviewFlag.CONFLICTING_IDENTITY)

    # Preserve order, drop duplicates.
    seen: set[ReviewFlag] = set()
    unique: list[ReviewFlag] = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            unique.append(flag)
    return unique


def _session_of(obj: Any) -> Optional[Session]:
    return Session.object_session(obj)


def _name_similarity(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    if not a or not b:
        return 0.0
    return max(fuzz.partial_ratio(a.lower(), b.lower()), fuzz.token_sort_ratio(a.lower(), b.lower())) / 100.0


def flags_to_json(flags: Iterable[ReviewFlag]) -> str:
    return json.dumps([f.value for f in flags])


def flags_from_json(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def apply_flags(
    submission: Submission,
    all_submissions: list[Submission],
    activity: Optional[Activity] = None,
    thresholds: Optional[dict[str, float]] = None,
) -> list[ReviewFlag]:
    """Recompute flags for a submission and update ``needs_review``."""
    flags = compute_review_flags(submission, all_submissions, activity, thresholds)
    submission.review_flags = flags_to_json(flags)
    if submission.reviewed_at is None:
        submission.needs_review = bool(flags)
    return flags


def build_review_queue(
    activity_id: int, db_session: Session, filters: Optional[dict] = None
) -> list[Submission]:
    """Return submissions for an activity, optionally filtered."""
    filters = filters or {}
    submissions = list(
        db_session.scalars(
            select(Submission)
            .where(Submission.activity_id == activity_id)
            .order_by(Submission.id)
        )
    )

    selected = filters.get("filter") or filters.get("status")
    if not selected or selected == "all":
        return submissions

    activity = db_session.get(Activity, activity_id)
    possible = float(activity.point_value) if activity else 1.0

    def has_flag(sub: Submission, flag: ReviewFlag) -> bool:
        return flag.value in flags_from_json(sub.review_flags)

    if selected == "needs_review":
        return [s for s in submissions if s.needs_review]
    if selected == "unmatched":
        return [s for s in submissions if s.effective_student_id is None]
    if selected == "low_confidence":
        return [s for s in submissions if has_flag(s, ReviewFlag.LOW_OCR_CONFIDENCE)]
    if selected == "duplicate":
        return [s for s in submissions if has_flag(s, ReviewFlag.DUPLICATE_STUDENT)]
    if selected == "correct":
        return [s for s in submissions if s.effective_score >= possible > 0]
    if selected == "incorrect":
        return [s for s in submissions if s.effective_score < possible]
    if selected == "missing":
        return [s for s in submissions if not s.effective_answer.strip()]
    if selected == "color":
        color = filters.get("color")
        if color:
            return [
                s for s in submissions if s.card is not None and s.card.color_category == color
            ]
        return [s for s in submissions if has_flag(s, ReviewFlag.UNUSUAL_COLOR)]
    return submissions


def color_breakdown(activity_id: int, db_session: Session) -> dict[str, int]:
    submissions = build_review_queue(activity_id, db_session)
    counter: Counter[str] = Counter()
    for submission in submissions:
        category = submission.card.color_category if submission.card else None
        counter[category or "unknown"] += 1
    return dict(counter)


def _is_correction(
    value: Optional[str], current: Optional[str], raw: Optional[str]
) -> bool:
    """True when ``value`` differs from both the stored and the recognised text."""
    if value is None:
        return False
    if current is not None:
        return value != current
    return value.strip() != (raw or "").strip()


def apply_correction(
    db_session: Session,
    submission: Submission,
    *,
    name: Optional[str] = None,
    email: Optional[str] = None,
    answer: Optional[str] = None,
    student_id: Optional[int] = None,
    score: Optional[float] = None,
    clear_score: bool = False,
    mark_reviewed: bool = True,
    actor: str = "instructor",
) -> Submission:
    """Record instructor corrections without ever overwriting raw OCR output.

    A value that is identical to the OCR result is not treated as a correction,
    so resubmitting the pre-filled review form does not fabricate an instructor
    edit. ``clear_score`` removes a previously stored score override.
    """
    from .grading import grade_answer
    from .activity import grading_rules_for

    changes: dict[str, Any] = {}

    if _is_correction(name, submission.instructor_name, submission.raw_name):
        submission.instructor_name = name
        changes["instructor_name"] = "set"
    if _is_correction(email, submission.instructor_email, submission.raw_email):
        submission.instructor_email = email
        changes["instructor_email"] = "set"
    if _is_correction(answer, submission.instructor_answer, submission.raw_answer):
        submission.instructor_answer = answer
        changes["instructor_answer"] = answer
    if student_id is not None and student_id != submission.instructor_student_id:
        submission.instructor_student_id = student_id or None
        submission.match_method = "instructor"
        submission.match_confidence = 1.0 if student_id else submission.match_confidence
        changes["instructor_student_id"] = student_id

    if clear_score and submission.instructor_score is not None:
        submission.instructor_score = None
        submission.instructor_score_changed = False
        changes["instructor_score"] = None
    elif score is not None and score != submission.instructor_score:
        submission.instructor_score = float(score)
        submission.instructor_score_changed = True
        changes["instructor_score"] = float(score)

    activity = db_session.get(Activity, submission.activity_id)
    if activity is not None:
        rules = grading_rules_for(activity)
        result = grade_answer(submission.effective_answer, rules)
        submission.auto_grade_rule = result.rule_used
        submission.final_score = (
            float(submission.instructor_score)
            if submission.instructor_score is not None
            else result.score
        )

    if mark_reviewed:
        submission.reviewed_at = utcnow()
        submission.needs_review = False
        changes["reviewed"] = True

    all_submissions = build_review_queue(submission.activity_id, db_session)
    apply_flags(submission, all_submissions, activity)
    if mark_reviewed:
        # Flags stay recorded for reporting, but a reviewed card leaves the queue.
        submission.needs_review = False

    if changes:
        audit.log_action(
            db_session,
            "submission",
            submission.id,
            "instructor_correction",
            actor,
            changes,
        )
    db_session.commit()
    return submission
