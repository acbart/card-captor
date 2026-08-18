"""Tests for roster import and student matching."""

from __future__ import annotations

from pathlib import Path

from cardcaptor.db.models import Student
from cardcaptor.services.roster import (
    import_roster_from_csv,
    is_valid_email,
    match_student,
    normalize_email,
)


def _student(sid: int, name: str, email: str) -> Student:
    return Student(id=sid, course_id=1, name=name, email=email)


ROSTER = [
    _student(1, "Alice Johnson", "alice.johnson@university.edu"),
    _student(2, "Bob Smith", "bob.smith@university.edu"),
    _student(3, "Carol Nguyen", "carol.nguyen@university.edu"),
]


def test_exact_email_match():
    match = match_student("Alice Johnson", "alice.johnson@university.edu", ROSTER)
    assert match.student_id == 1
    assert match.method == "exact_email"
    assert match.confidence >= 0.95
    assert match.ambiguous is False


def test_normalized_email_match():
    match = match_student("", " Alice.Johnson@University.EDU ", ROSTER)
    assert match.student_id == 1
    assert match.method in ("exact_email", "normalized_email")
    assert match.confidence >= 0.9


def test_fuzzy_email_match():
    # One OCR character error in the local part.
    match = match_student("", "bob.smtih@university.edu", ROSTER)
    assert match.student_id == 2
    assert match.method in ("fuzzy_email", "combined")
    assert 0.5 < match.confidence < 1.0


def test_combined_name_and_fuzzy_email():
    match = match_student("Bob Smith", "bob.smthi@university.edu", ROSTER)
    assert match.student_id == 2
    assert match.method in ("combined", "fuzzy_email")
    assert match.confidence > 0.7


def test_name_only_match():
    match = match_student("Carol Nguyen", "", ROSTER)
    assert match.student_id == 3
    assert match.method == "name_match"
    assert 0.0 < match.confidence < 0.9


def test_ambiguous_name_match():
    roster = [
        _student(1, "John Smith", "john.smith@university.edu"),
        _student(2, "Jon Smith", "jon.smith@university.edu"),
    ]
    match = match_student("Jon Smith", "", roster)
    assert match.ambiguous is True
    assert match.confidence <= 0.5
    assert len(match.candidates) == 2


def test_no_match_returns_zero_confidence():
    match = match_student("Zebulon Xylophone", "zzz@elsewhere.org", ROSTER)
    assert match.student_id is None
    assert match.confidence == 0.0
    assert match.method == "none"


def test_empty_input_returns_no_match():
    match = match_student("", "", ROSTER)
    assert match.student_id is None
    assert match.method == "none"


def test_email_validation_with_domain():
    assert is_valid_email("alice@university.edu", "university.edu") is True
    assert is_valid_email("alice@other.edu", "university.edu") is False
    assert is_valid_email("not-an-email", None) is False
    assert normalize_email("  Alice@University.EDU ") == "alice@university.edu"


def test_match_reports_invalid_email():
    match = match_student("Alice Johnson", "alice.johnson(at)university", ROSTER)
    assert match.email_valid is False


def test_import_roster_from_csv(db_session, course, tmp_path: Path):
    csv_path = tmp_path / "roster.csv"
    csv_path.write_text(
        "Student Name,Email Address,Canvas User ID,SIS ID\n"
        "Alice Johnson,alice.johnson@university.edu,1001,A1001\n"
        "Bob Smith,BOB.SMITH@university.edu,,A1002\n"
        "Carol Nguyen,,,A1003\n",
        encoding="utf-8",
    )

    count, warnings = import_roster_from_csv(csv_path, course.id, db_session)

    assert count == 3
    students = db_session.query(Student).filter(Student.course_id == course.id).all()
    assert len(students) == 3
    alice = next(s for s in students if s.name == "Alice Johnson")
    assert alice.canvas_user_id == "1001"
    assert alice.sis_id == "A1001"
    bob = next(s for s in students if s.name == "Bob Smith")
    assert bob.email == "bob.smith@university.edu"  # normalized
    assert any("no email" in w for w in warnings)


def test_import_roster_is_idempotent(db_session, course, tmp_path: Path):
    csv_path = tmp_path / "roster.csv"
    csv_path.write_text(
        "name,email\nAlice Johnson,alice.johnson@university.edu\n", encoding="utf-8"
    )
    import_roster_from_csv(csv_path, course.id, db_session)
    import_roster_from_csv(csv_path, course.id, db_session)
    students = db_session.query(Student).filter(Student.course_id == course.id).all()
    assert len(students) == 1


def test_import_roster_skips_duplicate_rows(db_session, course, tmp_path: Path):
    csv_path = tmp_path / "roster.csv"
    csv_path.write_text(
        "name,email\nA,a@university.edu\nA again,a@university.edu\n", encoding="utf-8"
    )
    count, warnings = import_roster_from_csv(csv_path, course.id, db_session)
    assert count == 1
    assert any("duplicate" in w for w in warnings)
