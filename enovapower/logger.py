"""Logging configuration for enovapower."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

LOGGER_NAME = "enovapower"

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """Get the enovapower logger.

    Returns the library logger without adding any handlers.
    Users can configure their own logging or use configure_logging()
    for default behavior.
    """
    global _logger
    if _logger is None:
        _logger = logging.getLogger(LOGGER_NAME)
    return _logger


def configure_logging(
    level: int = logging.DEBUG,
    handlers: Sequence[logging.Handler] | None = None,
    format_string: str | None = None,
) -> logging.Logger:
    """Configure the enovapower logger with default handlers.

    This is optional - users can configure logging themselves using
    Python's standard logging configuration.

    Args:
        level: Minimum log level to output (default: DEBUG).
        handlers: Custom handlers to use. If None, a default StreamHandler
            to stdout is created.
        format_string: Custom format string. If None, uses default format.

    Returns:
        The configured logger.
    """
    logger = get_logger()
    logger.setLevel(level)

    if handlers is None:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        if format_string is None:
            format_string = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

        formatter = logging.Formatter(
            fmt=format_string,
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handlers = [handler]

    for h in handlers:
        if h not in logger.handlers:
            logger.addHandler(h)

    return logger
