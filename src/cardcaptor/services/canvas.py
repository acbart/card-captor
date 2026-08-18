"""Canvas LMS integration.

The API token is read from the environment, sent only in the Authorization
header, and never written to logs, audit entries or reprs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

REDACTED = "REDACTED"
AUTH_SCHEME = "Bearer"


@dataclass
class CanvasConfig:
    base_url: str
    token: str
    course_id: str = ""

    def __repr__(self) -> str:
        return f"CanvasConfig(base_url={self.base_url!r}, token=REDACTED)"

    __str__ = __repr__

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)

    def headers(self) -> dict[str, str]:
        # The token only ever appears here - never in logs or reprs.
        scheme = AUTH_SCHEME
        return {"Authorization": f"{scheme} {self.token}", "Accept": "application/json"}

    def submission_url(self, assignment_id: str, user_id: str, course_id: str = "") -> str:
        course = course_id or self.course_id
        base = self.base_url.rstrip("/")
        return (
            f"{base}/api/v1/courses/{course}/assignments/{assignment_id}"
            f"/submissions/{user_id}"
        )


@dataclass
class CanvasGradeEntry:
    user_id: str
    score: float
    comment: Optional[str] = None

    def as_payload(self) -> dict:
        payload: dict = {"submission": {"posted_grade": self.score}}
        if self.comment:
            payload["comment"] = {"text_comment": self.comment}
        return payload

    def as_hashable(self) -> dict:
        return {
            "user_id": str(self.user_id),
            "score": round(float(self.score), 6),
            "comment": self.comment or "",
        }


@dataclass
class CanvasUploadResult:
    dry_run: bool
    entries: list[CanvasGradeEntry] = field(default_factory=list)
    payload_hash: str = ""
    success_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: bool = False

    @property
    def status(self) -> str:
        if self.error_count and self.success_count:
            return "partial"
        if self.error_count:
            return "failed"
        return "success"


def compute_payload_hash(assignment_id: str, entries: Sequence[CanvasGradeEntry]) -> str:
    """Stable hash of the grade payload, used for idempotency checks."""
    payload = {
        "assignment_id": str(assignment_id),
        "entries": sorted(
            (entry.as_hashable() for entry in entries), key=lambda e: e["user_id"]
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def upload_grades(
    config: CanvasConfig,
    assignment_id: str,
    entries: list[CanvasGradeEntry],
    dry_run: bool = True,
    previous_upload_hash: Optional[str] = None,
    course_id: str = "",
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 30.0,
) -> CanvasUploadResult:
    """Upload grades to Canvas.

    ``dry_run=True`` computes and logs the payload without contacting Canvas.
    If ``previous_upload_hash`` equals the computed hash, the upload is skipped.
    """
    payload_hash = compute_payload_hash(assignment_id, entries)
    result = CanvasUploadResult(
        dry_run=dry_run, entries=list(entries), payload_hash=payload_hash
    )

    if previous_upload_hash and previous_upload_hash == payload_hash:
        logger.info(
            "Canvas upload for assignment %s skipped: payload unchanged (%s)",
            assignment_id,
            payload_hash[:12],
        )
        result.skipped = True
        result.success_count = len(entries)
        return result

    if dry_run:
        logger.info(
            "[dry-run] Would upload %d grades to Canvas assignment %s (payload %s)",
            len(entries),
            assignment_id,
            payload_hash[:12],
        )
        for entry in entries:
            logger.debug(
                "[dry-run] user_id=%s score=%s comment=%s",
                entry.user_id,
                entry.score,
                bool(entry.comment),
            )
        result.success_count = len(entries)
        return result

    if not config.is_configured:
        result.error_count = len(entries)
        result.errors.append("Canvas is not configured (missing base URL or token)")
        return result

    course = course_id or config.course_id
    if not course:
        result.error_count = len(entries)
        result.errors.append("Canvas course id is required for a live upload")
        return result

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        for entry in entries:
            url = config.submission_url(assignment_id, entry.user_id, course)
            try:
                response = await http.put(
                    url, headers=config.headers(), json=entry.as_payload()
                )
                if response.status_code >= 400:
                    result.error_count += 1
                    result.errors.append(
                        f"user {entry.user_id}: HTTP {response.status_code}"
                    )
                else:
                    result.success_count += 1
            except httpx.HTTPError as exc:
                result.error_count += 1
                result.errors.append(f"user {entry.user_id}: {type(exc).__name__}")
    finally:
        if owns_client:
            await http.aclose()

    return result


def build_entries(
    submissions: Sequence,
    students_by_id: dict,
    mode: str = "direct",
    activity_name: str = "",
) -> tuple[list[CanvasGradeEntry], list[str]]:
    """Build Canvas grade entries from submissions.

    ``mode='accumulated'`` adds a per-activity comment so several activities can
    share one Canvas assignment.
    """
    entries: list[CanvasGradeEntry] = []
    warnings: list[str] = []
    for submission in submissions:
        student_id = submission.effective_student_id
        student = students_by_id.get(int(student_id)) if student_id else None
        if student is None:
            warnings.append("submission without a matched student was skipped")
            continue
        canvas_user_id = student.canvas_user_id or student.sis_id
        if not canvas_user_id:
            warnings.append(f"{student.name}: no Canvas user id, skipped")
            continue
        comment = (
            f"{activity_name}: {submission.effective_score:g} point(s)"
            if mode == "accumulated" and activity_name
            else None
        )
        entries.append(
            CanvasGradeEntry(
                user_id=str(canvas_user_id),
                score=float(submission.effective_score),
                comment=comment,
            )
        )
    return entries, warnings


def config_from_app_config(app_config, course_id: str = "") -> CanvasConfig:
    """Build a :class:`CanvasConfig` from the application configuration."""
    return CanvasConfig(
        base_url=app_config.canvas_base_url,
        token=app_config.canvas_token,
        course_id=course_id or "",
    )


async def upload_activity_grades(
    session,
    activity,
    config: Optional[CanvasConfig] = None,
    dry_run: bool = True,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[CanvasUploadResult, list[str]]:
    """Upload one activity's grades, recording the attempt in the database."""
    from sqlalchemy import select

    from ..config import get_config
    from ..db.models import CanvasUpload, Student, Submission, utcnow
    from . import audit

    app_config = get_config()
    course = activity.course
    config = config or config_from_app_config(
        app_config, course.canvas_course_id if course else ""
    )
    if course is not None and not config.course_id:
        config.course_id = course.canvas_course_id or ""

    assignment_id = activity.canvas_assignment_id or ""
    if not assignment_id:
        raise ValueError("Activity has no Canvas assignment id configured")

    submissions = list(
        session.scalars(
            select(Submission)
            .where(Submission.activity_id == activity.id)
            .order_by(Submission.id)
        )
    )
    students = {
        int(s.id): s
        for s in session.scalars(
            select(Student).where(Student.course_id == activity.course_id)
        )
    }

    entries, warnings = build_entries(
        submissions,
        students,
        mode=activity.canvas_assignment_mode or "direct",
        activity_name=activity.name,
    )

    previous = session.scalars(
        select(CanvasUpload)
        .where(
            CanvasUpload.activity_id == activity.id,
            CanvasUpload.dry_run.is_(False),
            CanvasUpload.status == "success",
        )
        .order_by(CanvasUpload.id.desc())
    ).first()
    previous_hash = previous.payload_hash if previous is not None else None

    result = await upload_grades(
        config,
        assignment_id,
        entries,
        dry_run=dry_run,
        previous_upload_hash=previous_hash if not dry_run else None,
        client=client,
    )

    record = CanvasUpload(
        activity_id=activity.id,
        dry_run=dry_run,
        submission_count=len(entries),
        payload_hash=result.payload_hash,
        canvas_assignment_id=assignment_id,
        status=result.status,
        error_message="; ".join(result.errors) if result.errors else None,
    )
    session.add(record)
    session.flush()

    if not dry_run and result.status in ("success", "partial"):
        activity.canvas_uploaded_at = utcnow()

    audit.log_action(
        session,
        "canvas_upload",
        record.id,
        "canvas_upload",
        "instructor",
        {
            "activity_id": activity.id,
            "assignment_id": assignment_id,
            "dry_run": dry_run,
            "entry_count": len(entries),
            "payload_hash": result.payload_hash,
            "status": result.status,
            "skipped": result.skipped,
        },
    )
    session.commit()
    return result, warnings
