"""Tests of the configuration sources and their priority."""

import logging
from pathlib import Path

import pytest

from med_analyzer.config import CONFIG_PATH_VAR, AppConfig, get_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test without config variables and with an empty cache."""
    for name in ('DATABASE_URL', 'STORAGE_DIR', 'MAX_UPLOAD_MB', CONFIG_PATH_VAR):
        monkeypatch.delenv(name, raising=False)
    get_config.cache_clear()
    yield
    get_config.cache_clear()


def write_yaml(path: Path) -> Path:
    """Write a config file with every required key."""
    path.write_text(
        'database_url: postgresql+psycopg://yaml/db\n'
        'storage_dir: /yaml/storage\n'
        'max_upload_mb: 3\n'
    )
    return path


def test_yaml_supplies_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(CONFIG_PATH_VAR, str(write_yaml(tmp_path / 'config.yml')))

    config = AppConfig()

    assert config.database_url == 'postgresql+psycopg://yaml/db'
    assert config.storage_dir == Path('/yaml/storage')
    assert config.max_upload_mb == 3


def test_env_overrides_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(CONFIG_PATH_VAR, str(write_yaml(tmp_path / 'config.yml')))
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://env/db')
    monkeypatch.setenv('MAX_UPLOAD_MB', '7')

    config = AppConfig()

    assert config.database_url == 'postgresql+psycopg://env/db'
    assert config.max_upload_mb == 7
    # Keys absent from the environment still come from the file.
    assert config.storage_dir == Path('/yaml/storage')


def test_env_only_without_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://env/db')
    monkeypatch.setenv('STORAGE_DIR', '/env/storage')

    config = AppConfig()

    assert config.database_url == 'postgresql+psycopg://env/db'
    assert config.storage_dir == Path('/env/storage')


def test_missing_config_file_is_reported_and_env_still_works(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    missing = tmp_path / 'absent.yml'
    monkeypatch.setenv(CONFIG_PATH_VAR, str(missing))
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://env/db')
    monkeypatch.setenv('STORAGE_DIR', '/env/storage')

    with caplog.at_level(logging.WARNING, logger='med_analyzer.config'):
        config = AppConfig()

    assert config.database_url == 'postgresql+psycopg://env/db'
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(missing) in warnings[0].getMessage()


def test_no_warning_when_config_path_is_unset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://env/db')
    monkeypatch.setenv('STORAGE_DIR', '/env/storage')

    with caplog.at_level(logging.WARNING, logger='med_analyzer.config'):
        AppConfig()

    assert not caplog.records
