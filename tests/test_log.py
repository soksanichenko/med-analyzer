"""Tests for the ``med_analyzer`` logger set-up done at application start."""

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from med_analyzer.app import create_app
from med_analyzer.config import get_config
from med_analyzer.log import PACKAGE_LOGGER, configure_logging, get_logger

LOG_FORMAT_PARTS = ('INFO', 'med_analyzer.uploads', 'hello from uploads')


@pytest.fixture(autouse=True)
def restore_package_logger() -> Iterator[None]:
    """Undo the handler and level that the code under test installs."""
    logger = logging.getLogger(PACKAGE_LOGGER)
    handlers, level = list(logger.handlers), logger.level
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)


def _run_app(monkeypatch: pytest.MonkeyPatch, level: str) -> None:
    monkeypatch.setenv('LOG_LEVEL', level)
    get_config.cache_clear()
    with TestClient(create_app()):
        get_logger('med_analyzer.uploads').info('hello from uploads')


def test_info_line_is_printed_at_info(
    web_env: None,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_app(monkeypatch, 'info')

    line = next(
        line for line in capsys.readouterr().err.splitlines() if 'hello' in line
    )
    assert all(part in line for part in LOG_FORMAT_PARTS)


def test_info_line_is_hidden_at_warning(
    web_env: None,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_app(monkeypatch, 'WARNING')

    assert 'hello from uploads' not in capsys.readouterr().err


def test_invalid_level_falls_back_to_info_with_a_warning(
    web_env: None,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_app(monkeypatch, 'LOUD')

    err = capsys.readouterr().err
    assert 'hello from uploads' in err
    assert err.count('WARNING') == 1


def test_repeated_app_starts_keep_one_handler(
    web_env: None,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for _ in range(3):
        _run_app(monkeypatch, 'INFO')

    assert len(logging.getLogger(PACKAGE_LOGGER).handlers) == 1
    assert capsys.readouterr().err.count('hello from uploads') == 3


def test_root_and_uvicorn_loggers_are_left_alone() -> None:
    root_handlers = list(logging.getLogger().handlers)
    uvicorn_handlers = list(logging.getLogger('uvicorn').handlers)

    configure_logging('DEBUG')

    assert logging.getLogger().handlers == root_handlers
    assert logging.getLogger('uvicorn').handlers == uvicorn_handlers
