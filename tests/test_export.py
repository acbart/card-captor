"""Tests for CSV export."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from cardcaptor.services.export import (
    AUDIT_COLUMNS,
    CANVAS_COLUMNS,
    export_audit_csv,
    export_both,
    export_canvas_csv,
)


def _rows(content: str) -> list[list[str]]:
    return list(csv.reader(StringIO(content)))


def test_canvas_csv_has_expected_columns(db_session, activity, students, make_submission):
    make_submission(student=students[0], raw_answer="B", score=1.0)
    submissions = [s for s in activity.submissions]
    content = export_canvas_csv(activity, submissions, students)
    rows = _rows(content)
    assert rows[0] == CANVAS_COLUMNS


def test_canvas_csv_uses_canvas_user_id_when_available(
    db_session, activity, students, make_submission
):
    make_submission(student=students[0], score=1.0)
    rows = _rows(export_canvas_csv(activity, list(activity.submissions), students))
    assert rows[1][0] == "Alice Johnson"
    assert rows[1][1] == "1001"  # canvas_user_id
    assert rows[1][2] == "A1001"  # sis id as SIS login
    assert rows[1][5] == "1"


def test_canvas_csv_falls_back_to_sis_then_email(db_session, activity, students, make_submission):
    make_submission(student=students[1], score=0.0)  # Bob has sis_id only
    make_submission(student=students[2], score=1.0)  # Carol has email only
    rows = _rows(export_canvas_csv(activity, list(activity.submissions), students))
    bob_row = next(r for r in rows[1:] if r[0] == "Bob Smith")
    carol_row = next(r for r in rows[1:] if r[0] == "Carol Nguyen")
    assert bob_row[1] == "A1002"
    assert bob_row[5] == "0"
    assert carol_row[1] == "carol.nguyen@university.edu"


def test_canvas_csv_marks_unmatched_submissions(db_session, activity, students, make_submission):
    make_submission(student=None, raw_name="", raw_answer="B", score=1.0)
    rows = _rows(export_canvas_csv(activity, list(activity.submissions), students))
    assert rows[1][0] == "Unmatched"
    assert rows[1][1] == ""


def test_instructor_score_overrides_auto_score(db_session, activity, students, make_submission):
    submission = make_submission(student=students[0], score=0.0)
    submission.instructor_score = 1.0
    submission.instructor_score_changed = True
    db_session.commit()
    rows = _rows(export_canvas_csv(activity, [submission], students))
    assert rows[1][5] == "1"


def test_audit_csv_columns_and_content(db_session, activity, students, make_submission):
    submission = make_submission(student=students[0], raw_answer="B", score=1.0, color="yellow")
    submission.instructor_answer = "B"
    db_session.commit()

    rows = _rows(export_audit_csv(activity, [submission], students))
    assert rows[0] == AUDIT_COLUMNS

    record = dict(zip(AUDIT_COLUMNS, rows[1]))
    assert record["student_name"] == "Alice Johnson"
    assert record["email"] == "alice.johnson@university.edu"
    assert record["activity"] == "Week 3 Quiz"
    assert record["activity_date"] == "2026-09-14"
    assert record["raw_answer"] == "B"
    assert record["score"] == "1"
    assert record["possible_points"] == "1"
    assert record["card_color"] == "yellow"
    assert record["match_method"] == "exact_email"
    assert record["instructor_corrected"] == "yes"
    assert record["card_uuid"].startswith("uuid-")


def test_export_both_writes_two_files(db_session, activity, students, make_submission, tmp_path: Path):
    make_submission(student=students[0], score=1.0)
    canvas_path, audit_path = export_both(activity.id, db_session, tmp_path)

    assert canvas_path.exists() and audit_path.exists()
    assert "canvas" in canvas_path.name and "audit" in audit_path.name
    assert _rows(canvas_path.read_text())[0] == CANVAS_COLUMNS
    assert _rows(audit_path.read_text())[0] == AUDIT_COLUMNS


def test_export_both_logs_to_audit_trail(db_session, activity, students, make_submission, tmp_path: Path):
    from cardcaptor.services.audit import get_entity_log

    make_submission(student=students[0], score=1.0)
    export_both(activity.id, db_session, tmp_path)
    entries = get_entity_log(db_session, "activity", activity.id)
    assert any(entry.action == "export" for entry in entries)


def test_csv_values_are_protected_against_formula_injection(
    db_session, activity, students, make_submission
) -> None:
    submission = make_submission(
        raw_name="=cmd|'/c calc'!A1",
        raw_email="alice.johnson@university.edu",
        raw_answer="B",
        student=None,
        score=1.0,
    )
    csv_text = export_audit_csv(activity, [submission], students)
    assert "'=cmd" in csv_text
    assert ",=cmd" not in csv_text


def test_numeric_values_are_not_escaped(
    db_session, activity, students, make_submission
) -> None:
    submission = make_submission(
        raw_name="Alice Johnson",
        raw_email="alice.johnson@university.edu",
        raw_answer="B",
        student=students[0],
        score=1.0,
    )
    csv_text = export_canvas_csv(activity, [submission], students)
    assert "'1" not in csv_text
