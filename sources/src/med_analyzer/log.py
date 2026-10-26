"""Logging helpers."""

import logging

PACKAGE_LOGGER = 'med_analyzer'
LOG_FORMAT = '%(asctime)s %(levelname)s %(name)s %(message)s'
DEFAULT_LEVEL = logging.INFO


class _PackageHandler(logging.StreamHandler):
    """Marks the handler installed by ``configure_logging``."""


def get_logger(logger_name: str) -> logging.Logger:
    """Return the logger registered under the given name.

    Args:
        logger_name: Usually ``__name__`` of the calling module.

    Returns:
        The standard library logger for that name.
    """
    return logging.getLogger(logger_name)


def configure_logging(level_name: str) -> None:
    """Send the ``med_analyzer`` loggers to stderr at the configured level.

    Safe to call repeatedly: the handler installed earlier is replaced, never
    duplicated. The root and uvicorn loggers are not touched, because uvicorn
    configures its own only and leaves ours silent otherwise.

    Args:
        level_name: Level name such as ``info`` (case-insensitive). An unknown
            name selects INFO and logs one warning.
    """
    logger = get_logger(PACKAGE_LOGGER)
    level = logging.getLevelNamesMapping().get(level_name.strip().upper())
    valid = level is not None and level != logging.NOTSET
    for old in [h for h in logger.handlers if isinstance(h, _PackageHandler)]:
        logger.removeHandler(old)
    # Created per call so it writes to the current ``sys.stderr``.
    handler = _PackageHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(level if valid and level is not None else DEFAULT_LEVEL)
    if not valid:
        logger.warning('Unknown LOG_LEVEL, using INFO')
