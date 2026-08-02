"""Release-safe console and persistent logging configuration."""

from __future__ import annotations

import sys
import traceback
from datetime import timedelta
from types import TracebackType
from typing import TYPE_CHECKING, TextIO
from uuid import UUID

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
        record["message"] = _redact_message(record, policy)
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
            settings.log_directory / config.LOG_FILENAME,
            level=settings.log_level,
            format=_FILE_FORMAT,
            filter=sanitize_record,
            colorize=False,
            encoding="utf-8",
            rotation=config.LOG_ROTATION_BYTES,
            retention=timedelta(days=config.LOG_RETENTION_DAYS),
            compression=None,
            backtrace=False,
            diagnose=False,
            enqueue=False,
            catch=False,
        )
        console = _available_stderr()
        if console is not None:
            logger.add(
                console,
                level=settings.log_level,
                format=_STDERR_FORMAT,
                filter=sanitize_record,
                colorize=True,
                backtrace=False,
                diagnose=False,
                enqueue=False,
                catch=False,
            )
    except Exception as error:
        logger.remove()
        raise ConfigurationError(
            "Persistent logging could not be configured; check the log directory."
        ) from error


def _available_stderr() -> TextIO | None:
    """Return a usable interpreter stderr without manufacturing a console stream."""
    for stream in (sys.stderr, sys.__stderr__):
        if stream is not None:
            return stream
    return None


def _redact_message(record: Record, policy: RedactionPolicy) -> str:
    """Redact a message while preserving one explicitly generated incident UUID."""
    incident_value = record["extra"].get("safe_incident_id")
    if not isinstance(incident_value, str) or not _canonical_uuid(incident_value):
        return policy.redact(record["message"], identifiers=True)
    marker = "MIRA_SAFE_INCIDENT_REFERENCE"
    protected = record["message"].replace(incident_value, marker)
    return policy.redact(protected, identifiers=True).replace(marker, incident_value)


def _canonical_uuid(value: str) -> bool:
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return str(parsed) == value


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
