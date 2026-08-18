"""Shared test fixtures."""

from __future__ import annotations

import datetime as dt
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cardcaptor.db.models import Activity, Base, Card, Course, SourceImage, Student, Submission


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def course(db_session) -> Course:
    course = Course(name="CS 101", term="Fall 2026", email_domain="university.edu")
    db_session.add(course)
    db_session.commit()
    return course


@pytest.fixture
def students(db_session, course) -> list[Student]:
    people = [
        Student(
            course_id=course.id,
            name="Alice Johnson",
            email="alice.johnson@university.edu",
            canvas_user_id="1001",
            sis_id="A1001",
        ),
        Student(
            course_id=course.id,
            name="Bob Smith",
            email="bob.smith@university.edu",
            sis_id="A1002",
        ),
        Student(
            course_id=course.id,
            name="Carol Nguyen",
            email="carol.nguyen@university.edu",
        ),
    ]
    db_session.add_all(people)
    db_session.commit()
    return people


@pytest.fixture
def activity(db_session, course) -> Activity:
    activity = Activity(
        course_id=course.id,
        name="Week 3 Quiz",
        date=dt.date(2026, 9, 14),
        point_value=1.0,
        expected_answers=json.dumps(["B"]),
        grading_rules=json.dumps({"rule_type": "mcq", "accepted_answers": ["B"]}),
        canvas_assignment_id="555",
    )
    db_session.add(activity)
    db_session.commit()
    return activity


@pytest.fixture
def make_submission(db_session, activity):
    """Factory creating a Card + Submission pair."""
    counter = {"n": 0}

    def _make(
        student=None,
        raw_name="",
        raw_email="",
        raw_answer="B",
        score=1.0,
        color="white",
        **kwargs,
    ) -> Submission:
        counter["n"] += 1
        index = counter["n"]
        image = SourceImage(
            activity_id=activity.id,
            original_filename=f"photo_{index}.jpg",
            stored_path=f"originals/photo_{index}.jpg",
            sha256=f"{index:064d}",
        )
        db_session.add(image)
        db_session.flush()

        card = Card(
            source_image_id=image.id,
            card_uuid=f"uuid-{index:04d}",
            crop_path=f"cards/activity_{activity.id}/uuid-{index:04d}.png",
            corner_points="[[0,0],[1,0],[1,1],[0,1]]",
            color_category=color,
            color_confidence=0.9,
        )
        db_session.add(card)
        db_session.flush()

        submission = Submission(
            activity_id=activity.id,
            card_id=card.id,
            student_id=student.id if student is not None else None,
            raw_name=raw_name or (student.name if student else ""),
            raw_email=raw_email or (student.email if student else ""),
            raw_answer=raw_answer,
            name_confidence=0.9,
            email_confidence=0.9,
            answer_confidence=0.9,
            match_confidence=1.0 if student is not None else 0.0,
            match_method="exact_email" if student is not None else "none",
            auto_score=score,
            final_score=score,
            needs_review=False,
            **kwargs,
        )
        db_session.add(submission)
        db_session.commit()
        return submission

    return _make
