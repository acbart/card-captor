"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..config import AppConfig, get_config
from .models import Base

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker[Session]] = None


def _enable_sqlite_fk(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_db_engine(url: str, echo: bool = False) -> Engine:
    """Create an engine with sane SQLite defaults."""
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, echo=echo, future=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_sqlite_fk)
    return engine


def get_engine(config: Optional[AppConfig] = None, refresh: bool = False) -> Engine:
    """Return (and lazily build) the process-wide engine."""
    global _engine, _session_factory
    if _engine is None or refresh:
        config = config or get_config()
        config.ensure_dirs()
        _engine = create_db_engine(config.db_url)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session_factory(config: Optional[AppConfig] = None) -> sessionmaker[Session]:
    get_engine(config)
    assert _session_factory is not None
    return _session_factory


def init_db(config: Optional[AppConfig] = None) -> Engine:
    """Create all tables if they do not already exist."""
    engine = get_engine(config, refresh=True)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(config: Optional[AppConfig] = None) -> Iterator[Session]:
    """Transactional scope around a series of operations."""
    factory = get_session_factory(config)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session(config: Optional[AppConfig] = None) -> Session:
    """Return a new session (caller is responsible for closing it)."""
    return get_session_factory(config)()


def reset_engine() -> None:
    """Drop cached engine/session factory (used by tests and the CLI)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
