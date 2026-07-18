"""Logging configuration."""

import sys

from loguru import logger

from app.core.settings import Settings


def configure_logging(settings: Settings) -> None:
    """Configure concise structured console logging."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}</cyan> | <level>{message}</level>"
        ),
    )
