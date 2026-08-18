"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "courses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("term", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("canvas_course_id", sa.String(length=64), nullable=True),
        sa.Column("email_domain", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "students",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("canvas_user_id", sa.String(length=64), nullable=True),
        sa.Column("sis_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_students_course_id", "students", ["course_id"])

    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("point_value", sa.Float(), nullable=False, server_default="1"),
        sa.Column("expected_answers", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("grading_rules", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("canvas_assignment_id", sa.String(length=64), nullable=True),
        sa.Column("canvas_assignment_mode", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finalized_at", sa.DateTime(), nullable=True),
        sa.Column("canvas_uploaded_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_activities_course_id", "activities", ["course_id"])

    op.create_table(
        "source_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("activity_id", sa.Integer(), sa.ForeignKey("activities.id"), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_source_images_activity_id", "source_images", ["activity_id"])
    op.create_index("ix_source_images_sha256", "source_images", ["sha256"])

    op.create_table(
        "cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_image_id", sa.Integer(), sa.ForeignKey("source_images.id"), nullable=False
        ),
        sa.Column("card_uuid", sa.String(length=36), nullable=False, unique=True),
        sa.Column("crop_path", sa.String(length=1024), nullable=False),
        sa.Column("corner_points", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("position_x", sa.Float(), nullable=False, server_default="0"),
        sa.Column("position_y", sa.Float(), nullable=False, server_default="0"),
        sa.Column("color_lab_l", sa.Float(), nullable=True),
        sa.Column("color_lab_a", sa.Float(), nullable=True),
        sa.Column("color_lab_b", sa.Float(), nullable=True),
        sa.Column("color_category", sa.String(length=64), nullable=True),
        sa.Column("color_confidence", sa.Float(), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cards_source_image_id", "cards", ["source_image_id"])

    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("activity_id", sa.Integer(), sa.ForeignKey("activities.id"), nullable=False),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("cards.id"), nullable=False, unique=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=True),
        sa.Column("raw_name", sa.Text(), nullable=True),
        sa.Column("raw_email", sa.Text(), nullable=True),
        sa.Column("raw_answer", sa.Text(), nullable=True),
        sa.Column("name_confidence", sa.Float(), nullable=True),
        sa.Column("email_confidence", sa.Float(), nullable=True),
        sa.Column("answer_confidence", sa.Float(), nullable=True),
        sa.Column("name_candidates", sa.Text(), nullable=True),
        sa.Column("email_candidates", sa.Text(), nullable=True),
        sa.Column("answer_candidates", sa.Text(), nullable=True),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("match_method", sa.String(length=64), nullable=True),
        sa.Column("instructor_name", sa.Text(), nullable=True),
        sa.Column("instructor_email", sa.Text(), nullable=True),
        sa.Column("instructor_answer", sa.Text(), nullable=True),
        sa.Column(
            "instructor_student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=True
        ),
        sa.Column("auto_score", sa.Float(), nullable=True),
        sa.Column("auto_grade_rule", sa.String(length=64), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("instructor_score", sa.Float(), nullable=True),
        sa.Column(
            "instructor_score_changed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("review_flags", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_submissions_activity_id", "submissions", ["activity_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("data", sa.Text(), nullable=False, server_default="{}"),
    )

    op.create_table(
        "canvas_uploads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("activity_id", sa.Integer(), sa.ForeignKey("activities.id"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("submission_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("canvas_assignment_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="success"),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_canvas_uploads_activity_id", "canvas_uploads", ["activity_id"])


def downgrade() -> None:
    op.drop_table("canvas_uploads")
    op.drop_table("audit_logs")
    op.drop_table("submissions")
    op.drop_table("cards")
    op.drop_table("source_images")
    op.drop_table("activities")
    op.drop_table("students")
    op.drop_table("courses")
