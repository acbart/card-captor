"""Tests for the review queue and instructor corrections."""

from __future__ import annotations

from cardcaptor.db.models import AuditLog
from cardcaptor.services.review import ReviewFlag, apply_correction, compute_review_flags


def test_resubmitting_ocr_values_is_not_recorded_as_a_correction(
    db_session, activity, students, make_submission
) -> None:
    submission = make_submission(
        raw_name="Alice Johnson",
        raw_email="alice.johnson@university.edu",
        raw_answer="B",
        student=students[0],
        score=1.0,
    )
    apply_correction(
        db_session,
        submission,
        name="Alice Johnson",
        email="alice.johnson@university.edu",
        answer="B",
    )

    assert submission.instructor_name is None
    assert submission.instructor_email is None
    assert submission.instructor_answer is None
    assert submission.raw_answer == "B"
    assert submission.reviewed_at is not None


def test_real_correction_is_stored_and_audited(
    db_session, activity, students, make_submission
) -> None:
    submission = make_submission(
        raw_name="A1ice J0hnson",
        raw_email="alice.johnson@university.edu",
        raw_answer="8",
        student=students[0],
        score=0.0,
    )
    apply_correction(db_session, submission, name="Alice Johnson", answer="B")

    assert submission.raw_name == "A1ice J0hnson"
    assert submission.raw_answer == "8"
    assert submission.instructor_name == "Alice Johnson"
    assert submission.instructor_answer == "B"
    assert submission.final_score == 1.0

    entries = db_session.query(AuditLog).filter_by(action="instructor_correction").all()
    assert len(entries) == 1


def test_score_override_can_be_applied_and_cleared(
    db_session, activity, students, make_submission
) -> None:
    submission = make_submission(
        raw_answer="B", student=students[0], score=1.0
    )
    apply_correction(db_session, submission, score=0.0)
    assert submission.instructor_score == 0.0
    assert submission.effective_score == 0.0

    apply_correction(db_session, submission, clear_score=True)
    assert submission.instructor_score is None
    assert submission.instructor_score_changed is False
    assert submission.effective_score == 1.0


def test_flags_report_unreadable_and_unmatched_submissions(
    db_session, activity, students, make_submission
) -> None:
    submission = make_submission(
        raw_name="", raw_email="", raw_answer="", student=None, score=0.0
    )
    submission.answer_confidence = 0.0
    flags = compute_review_flags(submission, [submission], activity)

    assert ReviewFlag.MISSING_NAME in flags
    assert ReviewFlag.MISSING_EMAIL in flags
    assert ReviewFlag.UNREADABLE_ANSWER in flags
    assert ReviewFlag.NO_ROSTER_MATCH in flags


def test_duplicate_student_is_flagged(
    db_session, activity, students, make_submission
) -> None:
    first = make_submission(raw_answer="B", student=students[0], score=1.0)
    second = make_submission(raw_answer="B", student=students[0], score=1.0)
    flags = compute_review_flags(first, [first, second], activity)

    assert ReviewFlag.DUPLICATE_STUDENT in flags
