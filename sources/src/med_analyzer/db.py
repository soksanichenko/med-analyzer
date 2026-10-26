"""Engine and session handling."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from med_analyzer.config import get_config


def make_session_factory(url: str) -> sessionmaker[Session]:
    """Create an engine for the URL and a session factory bound to it.

    Args:
        url: SQLAlchemy URL, e.g. ``postgresql+psycopg://user:pw@host/db``.

    Returns:
        A factory producing sessions that keep loaded attributes after commit.
    """
    engine = create_engine(url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)


@lru_cache
def _app_session_factory() -> sessionmaker[Session]:
    return make_session_factory(get_config().database_url)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding one session per request.

    Yields:
        A session that is closed when the request is finished.
    """
    with _app_session_factory()() as session:
        yield session
