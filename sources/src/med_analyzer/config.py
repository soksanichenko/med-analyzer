"""Application configuration: optional YAML file, environment overrides it."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    YamlConfigSettingsSource,
)

from med_analyzer.log import get_logger

logger = get_logger(__name__)

CONFIG_PATH_VAR = 'MED_ANALYZER_CONFIG'


class AppConfig(BaseSettings):
    """Settings of the web application.

    Values come from the YAML file named by ``MED_ANALYZER_CONFIG`` (optional)
    and from environment variables of the same name as the field in upper case
    (``DATABASE_URL``, ``STORAGE_DIR``, ...); the environment wins.
    """

    database_url: str
    storage_dir: Path
    max_upload_mb: int = 10
    log_level: str = 'INFO'

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Add the YAML file below the environment in the source priority.

        Args:
            settings_cls: The settings class being built.
            init_settings: Values passed to the constructor.
            env_settings: Environment variables.
            dotenv_settings: Unused ``.env`` file source.
            file_secret_settings: Unused secrets directory source.

        Returns:
            Sources ordered from the highest to the lowest priority.
        """
        # The path is read here, not at class creation, so tests can change it.
        yaml_file = os.environ.get(CONFIG_PATH_VAR)
        if yaml_file and not Path(yaml_file).is_file():
            # pydantic-settings skips a missing file silently, which hides a
            # typo in the path; the environment alone may still be enough.
            logger.warning('Config file %s does not exist, using env only', yaml_file)
        yaml_source = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file)
        return init_settings, env_settings, yaml_source


@lru_cache
def get_config() -> AppConfig:
    """Return the process-wide configuration, loading it on first use.

    Returns:
        The validated application settings.
    """
    config = AppConfig()
    logger.debug('Configuration loaded, storage_dir=%s', config.storage_dir)
    return config
