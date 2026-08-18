"""SQLAlchemy ORM models for card-captor."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base for all models."""


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    term: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    canvas_course_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    email_domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    students: Mapped[list["Student"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    activities: Mapped[list["Activity"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"Course(id={self.id}, name={self.name!r}, term={self.term!r})"


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    canvas_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sis_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    course: Mapped[Optional[Course]] = relationship(back_populates="students")

    def __repr__(self) -> str:
        return f"Student(id={self.id}, name={self.name!r})"


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    point_value: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    expected_answers: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    grading_rules: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    canvas_assignment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    canvas_assignment_mode: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finalized_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    canvas_uploaded_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)

    course: Mapped[Optional[Course]] = relationship(back_populates="activities")
    source_images: Mapped[list["SourceImage"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"Activity(id={self.id}, name={self.name!r})"


class SourceImage(Base):
    __tablename__ = "source_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    imported_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    activity: Mapped[Optional[Activity]] = relationship(back_populates="source_images")
    cards: Mapped[list["Card"]] = relationship(
        back_populates="source_image", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"SourceImage(id={self.id}, original_filename={self.original_filename!r})"


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_image_id: Mapped[int] = mapped_column(
        ForeignKey("source_images.id"), nullable=False, index=True
    )
    card_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    crop_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    corner_points: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    position_x: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    position_y: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    color_lab_l: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    color_lab_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    color_lab_b: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    color_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    color_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    detected_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    source_image: Mapped[Optional[SourceImage]] = relationship(back_populates="cards")
    submission: Mapped[Optional["Submission"]] = relationship(
        back_populates="card", cascade="all, delete-orphan", uselist=False
    )

    @property
    def crop_media_url(self) -> str:
        """URL for the extracted crop, served by the local web app."""
        relative = str(self.crop_path or "").replace("\\", "/")
        if "cards/" in relative:
            relative = relative.split("cards/", 1)[1]
        return f"/media/cards/{relative}"

    def __repr__(self) -> str:
        return f"Card(id={self.id}, card_uuid={self.card_uuid!r})"


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), nullable=False, index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), nullable=False, unique=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id"), nullable=True)

    raw_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    name_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    email_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    answer_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    name_candidates: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_candidates: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_candidates: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    match_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    match_method: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    instructor_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    instructor_email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    instructor_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    instructor_student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.id"), nullable=True
    )

    auto_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    auto_grade_rule: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    final_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    instructor_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    instructor_score_changed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    review_flags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    processed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    activity: Mapped[Optional[Activity]] = relationship(back_populates="submissions")
    card: Mapped[Optional[Card]] = relationship(back_populates="submission")
    student: Mapped[Optional[Student]] = relationship(foreign_keys=[student_id])
    instructor_student: Mapped[Optional[Student]] = relationship(
        foreign_keys=[instructor_student_id]
    )

    # -- convenience accessors ------------------------------------------
    @property
    def effective_name(self) -> str:
        return (self.instructor_name if self.instructor_name is not None else self.raw_name) or ""

    @property
    def effective_email(self) -> str:
        return (
            self.instructor_email if self.instructor_email is not None else self.raw_email
        ) or ""

    @property
    def effective_answer(self) -> str:
        return (
            self.instructor_answer if self.instructor_answer is not None else self.raw_answer
        ) or ""

    @property
    def effective_student_id(self) -> Optional[int]:
        return self.instructor_student_id if self.instructor_student_id else self.student_id

    @property
    def effective_score(self) -> float:
        if self.instructor_score is not None:
            return float(self.instructor_score)
        if self.final_score is not None:
            return float(self.final_score)
        return float(self.auto_score or 0.0)

    def __repr__(self) -> str:
        return f"Submission(id={self.id}, activity_id={self.activity_id}, card_id={self.card_id})"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    def __repr__(self) -> str:
        return (
            f"AuditLog(id={self.id}, entity_type={self.entity_type!r}, "
            f"entity_id={self.entity_id}, action={self.action!r})"
        )


class CanvasUpload(Base):
    __tablename__ = "canvas_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), nullable=False, index=True)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    submission_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    canvas_assignment_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="success")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    activity: Mapped[Optional[Activity]] = relationship()

    def __repr__(self) -> str:
        return f"CanvasUpload(id={self.id}, activity_id={self.activity_id}, status={self.status!r})"


__all__ = [
    "Base",
    "Course",
    "Student",
    "Activity",
    "SourceImage",
    "Card",
    "Submission",
    "AuditLog",
    "CanvasUpload",
    "utcnow",
]
