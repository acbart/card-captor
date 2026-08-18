"""Immutable audit logging."""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AuditLog

REDACTED = "REDACTED"
SENSITIVE_KEYS = {"token", "canvas_token", "password", "secret", "authorization", "api_key"}


def _scrub(value: Any) -> Any:
    """Recursively redact credential-like values before persisting."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if k.lower() in SENSITIVE_KEYS else _scrub(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def log_action(
    session: Session,
    entity_type: str,
    entity_id: int,
    action: str,
    actor: str = "system",
    data: Optional[dict] = None,
    commit: bool = False,
) -> AuditLog:
    """Append an entry to the audit log."""
    entry = AuditLog(
        entity_type=entity_type,
        entity_id=int(entity_id),
        action=action,
        actor=actor,
        data=json.dumps(_scrub(data or {}), default=str, sort_keys=True),
    )
    session.add(entry)
    session.flush()
    if commit:
        session.commit()
    return entry


def get_entity_log(
    session: Session, entity_type: str, entity_id: int, limit: int = 200
) -> list[AuditLog]:
    stmt = (
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == int(entity_id))
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def recent_log(session: Session, limit: int = 100) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    return list(session.scalars(stmt))
