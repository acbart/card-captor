"""Student roster import and OCR-to-roster matching."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from rapidfuzz import fuzz

from ..db.models import Student

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

EXACT_EMAIL_CONFIDENCE = 1.0
NORMALIZED_EMAIL_CONFIDENCE = 0.95
FUZZY_EMAIL_THRESHOLD = 0.85
NAME_THRESHOLD = 0.80
AMBIGUITY_RATIO = 0.90  # second-best within 10% of best -> ambiguous


@dataclass
class RosterMatch:
    student_id: Optional[int]
    confidence: float
    method: str
    ambiguous: bool = False
    candidates: list[tuple[int, float]] = field(default_factory=list)
    email_valid: bool = True

    @property
    def matched(self) -> bool:
        return self.student_id is not None and self.confidence > 0.0


def normalize_email(email: Optional[str]) -> str:
    text = re.sub(r"\s+", "", (email or "")).lower()
    text = text.replace("(at)", "@").replace("[at]", "@")
    return text


def normalize_name(name: Optional[str]) -> str:
    text = re.sub(r"[^\w\s'\-]", " ", (name or ""))
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def is_valid_email(email: Optional[str], email_domain: Optional[str] = None) -> bool:
    candidate = normalize_email(email)
    if not EMAIL_RE.match(candidate):
        return False
    if email_domain:
        domain = email_domain.strip().lstrip("@").lower()
        if not candidate.endswith("@" + domain):
            return False
    return True


def _email_score(raw_email: str, student_email: str) -> tuple[float, str]:
    if not raw_email or not student_email:
        return (0.0, "none")
    if raw_email.strip() == student_email.strip():
        return (EXACT_EMAIL_CONFIDENCE, "exact_email")
    if normalize_email(raw_email) == normalize_email(student_email):
        return (NORMALIZED_EMAIL_CONFIDENCE, "normalized_email")
    ratio = fuzz.token_sort_ratio(normalize_email(raw_email), normalize_email(student_email)) / 100.0
    if ratio >= FUZZY_EMAIL_THRESHOLD:
        return (round(ratio * 0.9, 4), "fuzzy_email")
    return (round(ratio * 0.3, 4), "weak_email")


def _name_score(raw_name: str, student_name: str) -> float:
    if not raw_name or not student_name:
        return 0.0
    a = normalize_name(raw_name)
    b = normalize_name(student_name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return max(fuzz.partial_ratio(a, b), fuzz.token_sort_ratio(a, b)) / 100.0


def _score_student(raw_name: str, raw_email: str, student: Student) -> tuple[float, str]:
    email_score, email_method = _email_score(raw_email, student.email or "")
    name_score = _name_score(raw_name, student.name or "")

    if email_method in ("exact_email", "normalized_email"):
        return (email_score, email_method)

    if email_method == "fuzzy_email":
        if name_score >= NAME_THRESHOLD:
            combined = min(0.95, 0.6 * email_score + 0.4 * name_score + 0.05)
            return (round(combined, 4), "combined")
        return (email_score, "fuzzy_email")

    if name_score >= NAME_THRESHOLD:
        return (round(name_score * 0.75, 4), "name_match")

    # Below every acceptance threshold: keep a weak score for candidate ranking only.
    return (round(max(email_score, name_score) * 0.25, 4), "none")


def match_student(
    raw_name: Optional[str],
    raw_email: Optional[str],
    roster: Sequence[Student],
    email_domain: Optional[str] = None,
) -> RosterMatch:
    """Match OCR'd name/email against the roster, conservatively."""
    name = (raw_name or "").strip()
    email = (raw_email or "").strip()
    email_valid = is_valid_email(email, email_domain) if email else False

    if not roster or (not name and not email):
        return RosterMatch(None, 0.0, "none", False, [], email_valid)

    scored: list[tuple[Student, float, str]] = []
    for student in roster:
        score, method = _score_student(name, email, student)
        scored.append((student, score, method))

    scored.sort(key=lambda item: item[1], reverse=True)
    candidates = [
        (int(s.id), float(score)) for s, score, _method in scored if score > 0.0
    ][:5]

    best_student, best_score, best_method = scored[0]
    if best_method == "none" or best_score <= 0.0:
        return RosterMatch(None, 0.0, "none", False, candidates, email_valid)

    ambiguous = False
    if len(scored) > 1:
        second_score = scored[1][1]
        if best_score > 0 and second_score / best_score >= AMBIGUITY_RATIO:
            ambiguous = True

    confidence = float(best_score)
    if ambiguous:
        confidence = min(confidence, 0.5)

    return RosterMatch(
        student_id=int(best_student.id),
        confidence=round(confidence, 4),
        method=best_method,
        ambiguous=ambiguous,
        candidates=candidates,
        email_valid=email_valid,
    )


# -- CSV import ---------------------------------------------------------
_COLUMN_ALIASES: dict[str, str] = {
    "name": "name",
    "student": "name",
    "student name": "name",
    "full name": "name",
    "fullname": "name",
    "display name": "name",
    "email": "email",
    "email address": "email",
    "e-mail": "email",
    "login id": "email",
    "sis login id": "email",
    "canvas_user_id": "canvas_user_id",
    "canvas user id": "canvas_user_id",
    "canvas id": "canvas_user_id",
    "id": "canvas_user_id",
    "user id": "canvas_user_id",
    "sis_id": "sis_id",
    "sis id": "sis_id",
    "sis user id": "sis_id",
    "student id": "sis_id",
}


def _normalize_header(header: str) -> str:
    key = re.sub(r"\s+", " ", (header or "")).strip().lower()
    return _COLUMN_ALIASES.get(key, key.replace(" ", "_"))


def _split_name(row: dict[str, str]) -> str:
    first = (row.get("first_name") or row.get("first") or "").strip()
    last = (row.get("last_name") or row.get("last") or row.get("surname") or "").strip()
    if first or last:
        return f"{first} {last}".strip()
    return ""


def parse_roster_rows(rows: Iterable[dict[str, str]]) -> tuple[list[dict[str, str]], list[str]]:
    """Normalize raw CSV rows into student dicts, collecting warnings."""
    parsed: list[dict[str, str]] = []
    warnings: list[str] = []
    seen_emails: set[str] = set()

    for index, raw_row in enumerate(rows, start=2):
        row = {
            _normalize_header(k): (v or "").strip()
            for k, v in raw_row.items()
            if k is not None
        }
        name = row.get("name") or _split_name(row)
        email = normalize_email(row.get("email", ""))

        if not name and not email:
            continue
        if not name:
            warnings.append(f"Row {index}: missing name, using email as name")
            name = email
        if not email:
            warnings.append(f"Row {index}: student {name!r} has no email address")
        elif not EMAIL_RE.match(email):
            warnings.append(f"Row {index}: invalid email {email!r} for {name!r}")

        if email and email in seen_emails:
            warnings.append(f"Row {index}: duplicate email {email!r} skipped")
            continue
        if email:
            seen_emails.add(email)

        parsed.append(
            {
                "name": name,
                "email": email,
                "canvas_user_id": row.get("canvas_user_id", "") or "",
                "sis_id": row.get("sis_id", "") or "",
            }
        )
    return parsed, warnings


def import_roster_from_csv(
    filepath: Path, course_id: int, db_session
) -> tuple[int, list[str]]:
    """Import students from a CSV file into a course.

    Existing students (same email) are updated rather than duplicated.
    Returns ``(count_imported, warnings)``.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Roster CSV not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    parsed, warnings = parse_roster_rows(rows)

    existing = {
        (s.email or "").lower(): s
        for s in db_session.query(Student).filter(Student.course_id == course_id).all()
    }

    imported = 0
    for entry in parsed:
        key = entry["email"].lower()
        student = existing.get(key) if key else None
        if student is None:
            student = Student(
                course_id=course_id,
                name=entry["name"],
                email=entry["email"],
                canvas_user_id=entry["canvas_user_id"] or None,
                sis_id=entry["sis_id"] or None,
            )
            db_session.add(student)
            if key:
                existing[key] = student
        else:
            student.name = entry["name"] or student.name
            student.canvas_user_id = entry["canvas_user_id"] or student.canvas_user_id
            student.sis_id = entry["sis_id"] or student.sis_id
        imported += 1

    db_session.flush()
    db_session.commit()
    return (imported, warnings)


def get_roster(db_session, course_id: int) -> list[Student]:
    return list(
        db_session.query(Student)
        .filter(Student.course_id == course_id)
        .order_by(Student.name)
        .all()
    )
