"""Release-safe console and persistent logging configuration."""

from __future__ import annotations

import sys
import traceback
from datetime import timedelta
from types import TracebackType
from typing import TYPE_CHECKING

from loguru import logger

from app.core import config
from app.core.exceptions import ConfigurationError
from app.core.redaction import RedactionPolicy
from app.core.settings import Settings

if TYPE_CHECKING:
    from loguru import Record

_STDERR_FORMAT = (
    "<green>{time:YYYY-MM-DDTHH:mm:ss.SSS!UTC}Z</green> | "
    "<level>{level: <8}</level> | <cyan>{module}</cyan> | "
    "<level>{message}</level>{extra[safe_exception]}"
)
_FILE_FORMAT = (
    "{time:YYYY-MM-DDTHH:mm:ss.SSSSSS!UTC}Z | {level: <8} | "
    "{module}:{function}:{line} | {message}{extra[safe_exception]}"
)


def configure_logging(settings: Settings) -> None:
    """Configure idempotent sanitized stderr and persistent file sinks."""
    logger.remove()
    policy = RedactionPolicy.from_settings(settings)

    def sanitize_record(record: Record) -> bool:
        record["message"] = policy.redact(record["message"], identifiers=True)
        if "safe_exception" not in record["extra"]:
            record["extra"]["safe_exception"] = _safe_exception_text(
                record["exception"],
                policy,
            )
        record["exception"] = None
        return True

    try:
        if not settings.log_directory.is_dir():
            raise OSError("The configured log directory is unavailable.")
        logger.add(
            sys.stderr,
            level=settings.log_level,
            format=_STDERR_FORMAT,
            filter=sanitize_record,
            colorize=True,
            backtrace=False,
            diagnose=False,
            enqueue=False,
            catch=False,
        )
        logger.add(
            settings.log_directory / config.LOG_FILENAME,
            level=settings.log_level,
            format=_FILE_FORMAT,
            filter=sanitize_record,
            colorize=False,
            encoding="utf-8",
            rotation=config.LOG_ROTATION_BYTES,
            retention=timedelta(days=config.LOG_RETENTION_DAYS),
            compression=None,
            enqueue=False,
            backtrace=False,
            diagnose=False,
            catch=False,
        )
    except Exception as error:
        logger.remove()
        raise ConfigurationError(
            "Persistent logging could not be configured; check the log directory."
        ) from error


def _safe_exception_text(
    exception: tuple[
        type[BaseException] | None,
        BaseException | None,
        TracebackType | None,
    ]
    | None,
    policy: RedactionPolicy,
) -> str:
    if exception is None:
        return ""
    exception_type, _, exception_traceback = exception
    class_name = exception_type.__name__ if exception_type is not None else "Exception"
    if exception_traceback is None:
        return f"\n{class_name}"
    frames = traceback.extract_tb(exception_traceback)
    safe_frames = (
        f"{policy.redact(frame.filename)}:{frame.lineno} in {frame.name}" for frame in frames
    )
    return f"\n{class_name}\n" + "\n".join(safe_frames)


__all__ = ["configure_logging"]
