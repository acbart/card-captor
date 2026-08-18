"""CSV export: Canvas grade upload format and a detailed audit trail."""

from __future__ import annotations

import csv
import datetime as dt
import re
from io import StringIO
from pathlib import Path
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Activity, Student, Submission
from . import audit
from .review import flags_from_json

CANVAS_COLUMNS = [
    "Student",
    "ID",
    "SIS Login ID",
    "Section",
    "Manual Posting",
    "Final Score",
]

AUDIT_COLUMNS = [
    "student_name",
    "email",
    "student_id",
    "activity",
    "activity_date",
    "raw_answer",
    "recognized_answer",
    "score",
    "possible_points",
    "reviewed",
    "card_color",
    "match_method",
    "ocr_confidence",
    "instructor_corrected",
    "card_uuid",
]


def _student_index(students: Sequence[Student]) -> dict[int, Student]:
    return {int(s.id): s for s in students}


def _canvas_id(student: Optional[Student]) -> str:
    if student is None:
        return ""
    return str(student.canvas_user_id or student.sis_id or student.email or "")


def _sis_login(student: Optional[Student]) -> str:
    if student is None:
        return ""
    return str(student.sis_id or student.email or "")


def _mean_confidence(submission: Submission) -> float:
    values = [
        v
        for v in (
            submission.name_confidence,
            submission.email_confidence,
            submission.answer_confidence,
        )
        if v is not None
    ]
    return round(sum(values) / len(values), 3) if values else 0.0


def _is_corrected(submission: Submission) -> bool:
    return any(
        value is not None
        for value in (
            submission.instructor_name,
            submission.instructor_email,
            submission.instructor_answer,
            submission.instructor_student_id,
            submission.instructor_score,
        )
    )


def _csv_safe(value: object) -> str:
    """Neutralise spreadsheet formula injection in exported values.

    OCR text and imported roster data are outside the operator's control, so a
    leading ``= + - @`` (or control character) is prefixed with an apostrophe to
    stop Excel/LibreOffice/Sheets from evaluating it as a formula.
    """
    text = "" if value is None else str(value)
    if text[:1] not in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return text
    try:
        float(text)
    except ValueError:
        return "'" + text
    return text


class _SafeWriter:
    """CSV writer that escapes formula-like values in data rows."""

    def __init__(self, buffer: StringIO) -> None:
        self._writer = csv.writer(buffer, lineterminator="\n")

    def writeheader(self, row: Sequence[object]) -> None:
        self._writer.writerow(list(row))

    def writerow(self, row: Sequence[object]) -> None:
        self._writer.writerow([_csv_safe(value) for value in row])


def export_canvas_csv(
    activity: Activity, submissions: Sequence[Submission], students: Sequence[Student]
) -> str:
    """Generate a Canvas-compatible grade CSV."""
    index = _student_index(students)
    buffer = StringIO()
    writer = _SafeWriter(buffer)
    writer.writeheader(CANVAS_COLUMNS)

    for submission in submissions:
        student_id = submission.effective_student_id
        student = index.get(int(student_id)) if student_id else None
        name = student.name if student else (submission.effective_name or "Unmatched")
        writer.writerow(
            [
                name,
                _canvas_id(student),
                _sis_login(student),
                "",
                "",
                f"{submission.effective_score:g}",
            ]
        )
    return buffer.getvalue()


def export_audit_csv(
    activity: Activity, submissions: Sequence[Submission], students: Sequence[Student]
) -> str:
    """Generate a detailed audit CSV for the instructor's records."""
    index = _student_index(students)
    buffer = StringIO()
    writer = _SafeWriter(buffer)
    writer.writeheader(AUDIT_COLUMNS)

    activity_date = activity.date.isoformat() if activity.date else ""
    possible = float(activity.point_value or 0.0)

    for submission in submissions:
        student_id = submission.effective_student_id
        student = index.get(int(student_id)) if student_id else None
        card = submission.card
        writer.writerow(
            [
                student.name if student else submission.effective_name,
                student.email if student else submission.effective_email,
                student.id if student else "",
                activity.name,
                activity_date,
                submission.raw_answer or "",
                submission.effective_answer,
                f"{submission.effective_score:g}",
                f"{possible:g}",
                "yes" if submission.reviewed_at else "no",
                (card.color_category if card else "") or "",
                submission.match_method or "none",
                f"{_mean_confidence(submission)}",
                "yes" if _is_corrected(submission) else "no",
                card.card_uuid if card else "",
            ]
        )
    return buffer.getvalue()


def export_flagged_csv(
    activity: Activity, submissions: Sequence[Submission]
) -> str:
    """CSV of submissions still flagged for review."""
    buffer = StringIO()
    writer = _SafeWriter(buffer)
    writer.writeheader(["card_uuid", "raw_name", "raw_email", "raw_answer", "flags"])
    for submission in submissions:
        flags = flags_from_json(submission.review_flags)
        if not flags:
            continue
        writer.writerow(
            [
                submission.card.card_uuid if submission.card else "",
                submission.raw_name or "",
                submission.raw_email or "",
                submission.raw_answer or "",
                ";".join(flags),
            ]
        )
    return buffer.getvalue()


def _slug(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (text or "activity")).strip("_").lower() or "activity"


def _safe_output_path(output_dir: Path, filename: str) -> Path:
    """Join a generated filename to ``output_dir``, refusing to escape it."""
    name = re.sub(r"[^\w\-.]+", "_", Path(filename).name).strip("._") or "export.csv"
    path = (output_dir / name[:200]).resolve()
    if path.parent != output_dir:
        raise ValueError(f"Refusing to write outside the export directory: {filename}")
    return path


def export_both(
    activity_id: int, db_session: Session, output_dir: Path
) -> tuple[Path, Path]:
    """Write both CSVs to ``output_dir`` and return their paths."""
    activity = db_session.get(Activity, activity_id)
    if activity is None:
        raise LookupError(f"No activity with id {activity_id}")

    submissions = list(
        db_session.scalars(
            select(Submission)
            .where(Submission.activity_id == activity_id)
            .order_by(Submission.id)
        )
    )
    students = list(
        db_session.scalars(select(Student).where(Student.course_id == activity.course_id))
    )

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slug(activity.name)

    canvas_path = _safe_output_path(output_dir, f"{slug}_canvas_{stamp}.csv")
    audit_path = _safe_output_path(output_dir, f"{slug}_audit_{stamp}.csv")

    canvas_path.write_text(
        export_canvas_csv(activity, submissions, students), encoding="utf-8"
    )
    audit_path.write_text(
        export_audit_csv(activity, submissions, students), encoding="utf-8"
    )

    audit.log_action(
        db_session,
        "activity",
        activity.id,
        "export",
        "instructor",
        {
            "canvas_csv": canvas_path.name,
            "audit_csv": audit_path.name,
            "submission_count": len(submissions),
        },
    )
    db_session.commit()
    return (canvas_path, audit_path)
