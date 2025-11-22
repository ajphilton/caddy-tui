"""Database bootstrap helpers."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from . import models
from .config import DB_PATH, ensure_app_dir


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine(db_path: Path | str | None = None):
    """Return a singleton engine for the configured DB path."""
    global _engine, _SessionLocal
    if _engine is None:
        ensure_app_dir()
        path = Path(db_path or DB_PATH)
        _engine = create_engine(f"sqlite:///{path}", future=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db(db_path: Path | str | None = None) -> None:
    """Create all tables."""
    engine = get_engine(db_path=db_path)
    models.Base.metadata.create_all(engine)


@contextmanager
def session_scope(db_path: Path | str | None = None) -> Iterator[Session]:
    """Provide a transactional scope."""
    if _SessionLocal is None:
        get_engine(db_path=db_path)
    assert _SessionLocal is not None  # safety
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:  # pragma: no cover
        session.rollback()
        raise
    finally:
        session.close()
