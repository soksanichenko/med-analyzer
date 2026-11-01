"""Shared fixtures: a throwaway Postgres container and per-test sessions."""

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from med_analyzer.app import create_app
from med_analyzer.config import CONFIG_PATH_VAR, get_config
from med_analyzer.db import get_db, make_session_factory
from med_analyzer.models import Base, User

SOURCES_DIR = Path(__file__).resolve().parents[1] / 'sources'


@pytest.fixture(scope='session')
def pg_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Start Postgres, migrate it to head and yield its SQLAlchemy URL."""
    storage_dir = tmp_path_factory.mktemp('storage')
    with PostgresContainer('postgres:18', driver='psycopg') as container:
        url = container.get_connection_url()
        with pytest.MonkeyPatch.context() as patch:
            # alembic/env.py takes the URL from the app config, like production.
            patch.setenv('DATABASE_URL', url)
            patch.setenv('STORAGE_DIR', str(storage_dir))
            get_config.cache_clear()
            alembic_cfg = Config(str(SOURCES_DIR / 'alembic.ini'))
            # The ini path is relative to the working directory; pin it.
            alembic_cfg.set_main_option(
                'script_location', str(SOURCES_DIR / 'src/med_analyzer/alembic')
            )
            # Keep pytest's log capture: skip fileConfig() in env.py.
            alembic_cfg.attributes['configure_logger'] = False
            command.upgrade(alembic_cfg, 'head')
            get_config.cache_clear()
        yield url


@pytest.fixture
def db(pg_url: str) -> Iterator[Session]:
    """Yield a session and empty every table afterwards."""
    factory = make_session_factory(pg_url)
    with factory() as session:
        yield session
        session.rollback()
        tables = ', '.join(table.name for table in Base.metadata.sorted_tables)
        session.execute(text(f'TRUNCATE {tables} RESTART IDENTITY CASCADE'))
        session.commit()
        engine = session.get_bind()
    engine.dispose()


@pytest.fixture
def make_user(db: Session) -> Callable[..., User]:
    """Return a factory that commits a new user."""

    def _make_user(
        discord_id: str = 'u1',
        sex: str | None = 'M',
        birth_date: date | None = date(1989, 1, 1),
    ) -> User:
        user = User(discord_user_id=discord_id, sex=sex, birth_date=birth_date)
        db.add(user)
        db.commit()
        return user

    return _make_user


@pytest.fixture
def web_env(
    pg_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point the app configuration at the test database."""
    monkeypatch.delenv(CONFIG_PATH_VAR, raising=False)
    monkeypatch.setenv('DATABASE_URL', pg_url)
    monkeypatch.setenv('STORAGE_DIR', str(tmp_path / 'storage'))
    get_config.cache_clear()
    yield
    get_config.cache_clear()


@pytest.fixture
def client(web_env: None, db: Session) -> Iterator[TestClient]:
    """Run the app with its lifespan, serving requests from the test session."""
    app = create_app()

    def _get_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _get_db
    # Most tests check the Russian texts; the language tests change the cookie.
    with TestClient(app, cookies={'lang': 'ru'}) as test_client:
        yield test_client
