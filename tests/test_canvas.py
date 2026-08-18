"""Tests for the Canvas integration."""

from __future__ import annotations

import httpx
import pytest

from cardcaptor.services.canvas import (
    CanvasConfig,
    CanvasGradeEntry,
    build_entries,
    compute_payload_hash,
    upload_grades,
)


@pytest.fixture
def config() -> CanvasConfig:
    return CanvasConfig(
        base_url="https://canvas.university.edu", token="secret-token-value", course_id="42"
    )


@pytest.fixture
def entries() -> list[CanvasGradeEntry]:
    return [
        CanvasGradeEntry(user_id="1001", score=1.0),
        CanvasGradeEntry(user_id="1002", score=0.0),
    ]


def test_config_repr_redacts_token(config):
    text = repr(config)
    assert "secret-token-value" not in text
    assert "REDACTED" in text
    assert "secret-token-value" not in str(config)
    assert "secret-token-value" not in f"{config}"


def test_headers_contain_the_token_only_there(config):
    headers = config.headers()
    assert headers["Authorization"].endswith("secret-token-value")
    assert "secret-token-value" not in repr(config)


async def test_dry_run_never_calls_http(config, entries, monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("dry run must not touch the network")

    monkeypatch.setattr(httpx.AsyncClient, "put", _boom)
    monkeypatch.setattr(httpx.AsyncClient, "request", _boom)

    result = await upload_grades(config, "555", entries, dry_run=True)

    assert result.dry_run is True
    assert result.success_count == 2
    assert result.error_count == 0
    assert result.payload_hash


def test_payload_hash_is_stable_and_order_independent(entries):
    hash_a = compute_payload_hash("555", entries)
    hash_b = compute_payload_hash("555", list(reversed(entries)))
    assert hash_a == hash_b
    assert len(hash_a) == 64


def test_payload_hash_changes_with_scores(entries):
    original = compute_payload_hash("555", entries)
    changed = compute_payload_hash(
        "555", [CanvasGradeEntry(user_id="1001", score=0.5), entries[1]]
    )
    assert original != changed
    assert compute_payload_hash("556", entries) != original


async def test_idempotency_skips_matching_payload(config, entries):
    payload_hash = compute_payload_hash("555", entries)

    async def _fail(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("upload must be skipped")

    transport = httpx.MockTransport(_fail)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await upload_grades(
            config,
            "555",
            entries,
            dry_run=False,
            previous_upload_hash=payload_hash,
            client=client,
        )

    assert result.skipped is True
    assert result.success_count == 2
    assert result.error_count == 0


async def test_live_upload_puts_one_request_per_entry(config, entries):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["Authorization"].endswith("secret-token-value")
        return httpx.Response(200, json={"id": 1})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await upload_grades(
            config, "555", entries, dry_run=False, previous_upload_hash="different", client=client
        )

    assert result.success_count == 2
    assert result.error_count == 0
    assert seen[0].endswith("/api/v1/courses/42/assignments/555/submissions/1001")


async def test_live_upload_records_errors(config, entries):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": "unauthorized"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await upload_grades(config, "555", entries, dry_run=False, client=client)

    assert result.success_count == 0
    assert result.error_count == 2
    assert result.status == "failed"
    assert all("401" in error for error in result.errors)


async def test_unconfigured_canvas_fails_cleanly(entries):
    result = await upload_grades(
        CanvasConfig(base_url="", token=""), "555", entries, dry_run=False
    )
    assert result.error_count == len(entries)
    assert "not configured" in result.errors[0]


def test_build_entries_skips_students_without_canvas_id(
    db_session, activity, students, make_submission
):
    make_submission(student=students[0], score=1.0)  # has canvas_user_id
    make_submission(student=students[1], score=0.0)  # sis_id only -> used as id
    make_submission(student=students[2], score=1.0)  # no canvas id at all
    make_submission(student=None, score=0.0)  # unmatched

    by_id = {int(s.id): s for s in students}
    built, warnings = build_entries(
        list(activity.submissions), by_id, mode="direct", activity_name="Week 3 Quiz"
    )

    assert [e.user_id for e in built] == ["1001", "A1002"]
    assert len(warnings) == 2
    assert all(e.comment is None for e in built)


def test_build_entries_accumulated_mode_adds_comment(
    db_session, activity, students, make_submission
):
    make_submission(student=students[0], score=1.0)
    by_id = {int(s.id): s for s in students}
    built, _ = build_entries(
        list(activity.submissions), by_id, mode="accumulated", activity_name="Week 3 Quiz"
    )
    assert built[0].comment == "Week 3 Quiz: 1 point(s)"


async def test_upload_activity_grades_records_upload(db_session, activity, students, make_submission):
    from cardcaptor.db.models import CanvasUpload
    from cardcaptor.services.canvas import upload_activity_grades

    make_submission(student=students[0], score=1.0)
    result, warnings = await upload_activity_grades(db_session, activity, dry_run=True)

    record = db_session.query(CanvasUpload).one()
    assert record.activity_id == activity.id
    assert record.dry_run is True
    assert record.payload_hash == result.payload_hash
    assert record.canvas_assignment_id == "555"
    assert record.status == "success"
    assert activity.canvas_uploaded_at is None  # dry run must not mark upload


async def test_upload_activity_grades_requires_assignment_id(db_session, activity):
    from cardcaptor.services.canvas import upload_activity_grades

    activity.canvas_assignment_id = None
    db_session.commit()
    with pytest.raises(ValueError):
        await upload_activity_grades(db_session, activity, dry_run=True)


def test_url_import_rejects_unsafe_urls() -> None:
    from cardcaptor.services.import_ import validate_import_url

    for url in ("ftp://example.com/a.png", "http://127.0.0.1/a.png", "http://[::1]/a.png"):
        with pytest.raises(ValueError):
            validate_import_url(url)
