"""Privacy-safe incident records shared by desktop resilience layers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Final
from uuid import UUID, uuid4

_MAX_IDENTIFIER_LENGTH: Final = 96
_MAX_EXCEPTION_TYPE_LENGTH: Final = 128
_MAX_SUMMARY_LENGTH: Final = 200
_MAX_FILENAME_LENGTH: Final = 128
_MAX_FRAMES: Final = 16
_UNSAFE_IDENTIFIER: Final = re.compile(r"[^A-Za-z0-9_.<>-]")


class IncidentPhase(StrEnum):
    """One explicit startup or runtime phase in which an incident occurred."""

    STARTUP_SETTINGS = "startup_settings"
    STARTUP_DIRECTORIES = "startup_directories"
    STARTUP_LOGGING = "startup_logging"
    STARTUP_RESTORE = "startup_restore"
    STARTUP_DATABASE_PREPARATION = "startup_database_preparation"
    STARTUP_DATABASE_INITIALIZATION = "startup_database_initialization"
    STARTUP_CONTAINER = "startup_container"
    STARTUP_QT = "startup_qt"
    STARTUP_THEME = "startup_theme"
    STARTUP_UI = "startup_ui"
    RUNTIME_QT_EVENT = "runtime_qt_event"
    RUNTIME_MAIN_THREAD = "runtime_main_thread"
    RUNTIME_BACKGROUND_THREAD = "runtime_background_thread"
    RUNTIME_ASYNCIO = "runtime_asyncio"
    RUNTIME_UNRAISABLE = "runtime_unraisable"


class IncidentSeverity(StrEnum):
    """Whether an incident requires application termination."""

    FATAL = "fatal"
    NON_FATAL = "non_fatal"


_SAFE_SUMMARIES: Final[dict[IncidentPhase, str]] = {
    IncidentPhase.STARTUP_SETTINGS: "Application settings could not be loaded.",
    IncidentPhase.STARTUP_DIRECTORIES: "Application directories could not be prepared.",
    IncidentPhase.STARTUP_LOGGING: "Application logging could not be prepared.",
    IncidentPhase.STARTUP_RESTORE: "Pending database recovery could not be completed.",
    IncidentPhase.STARTUP_DATABASE_PREPARATION: "The database could not be prepared.",
    IncidentPhase.STARTUP_DATABASE_INITIALIZATION: "The database could not be initialized.",
    IncidentPhase.STARTUP_CONTAINER: "Application services could not be initialized.",
    IncidentPhase.STARTUP_QT: "The desktop runtime could not be initialized.",
    IncidentPhase.STARTUP_THEME: "The desktop appearance could not be initialized.",
    IncidentPhase.STARTUP_UI: "The main window could not be initialized.",
    IncidentPhase.RUNTIME_QT_EVENT: "A fatal desktop event error occurred.",
    IncidentPhase.RUNTIME_MAIN_THREAD: "A fatal application error occurred.",
    IncidentPhase.RUNTIME_BACKGROUND_THREAD: "A fatal background error occurred.",
    IncidentPhase.RUNTIME_ASYNCIO: "A fatal asynchronous error occurred.",
    IncidentPhase.RUNTIME_UNRAISABLE: "A non-fatal interpreter cleanup error occurred.",
}


@dataclass(frozen=True, slots=True)
class SafeFrame:
    """Bounded traceback metadata without source paths or local values."""

    module_name: str
    function_name: str
    filename: str
    line_number: int

    def __post_init__(self) -> None:
        _require_safe_identifier("module_name", self.module_name)
        _require_safe_identifier("function_name", self.function_name)
        if not self.filename or len(self.filename) > _MAX_FILENAME_LENGTH:
            raise ValueError("filename must be present and bounded.")
        if (
            Path(self.filename).name != self.filename
            or "/" in self.filename
            or "\\" in self.filename
        ):
            raise ValueError("filename must be a source basename.")
        if self.line_number < 1:
            raise ValueError("line_number must be positive.")


@dataclass(frozen=True, slots=True)
class ErrorIncident:
    """Immutable, privacy-safe description of one application error."""

    incident_id: UUID
    occurred_at_utc: datetime
    phase: IncidentPhase
    severity: IncidentSeverity
    exception_type: str
    safe_summary: str
    safe_frames: tuple[SafeFrame, ...]

    def __post_init__(self) -> None:
        if self.incident_id.version is None or str(UUID(str(self.incident_id))) != str(
            self.incident_id
        ):
            raise ValueError("incident_id must be a canonical UUID.")
        if self.occurred_at_utc.tzinfo is None or self.occurred_at_utc.utcoffset() is None:
            raise ValueError("occurred_at_utc must be timezone-aware.")
        if self.occurred_at_utc.utcoffset() != UTC.utcoffset(self.occurred_at_utc):
            raise ValueError("occurred_at_utc must use UTC.")
        if not self.exception_type or len(self.exception_type) > _MAX_EXCEPTION_TYPE_LENGTH:
            raise ValueError("exception_type must be present and bounded.")
        if _UNSAFE_IDENTIFIER.search(self.exception_type) is not None:
            raise ValueError("exception_type contains unsafe characters.")
        if not self.safe_summary or len(self.safe_summary) > _MAX_SUMMARY_LENGTH:
            raise ValueError("safe_summary must be present and bounded.")
        if len(self.safe_frames) > _MAX_FRAMES:
            raise ValueError("safe_frames exceeds the incident frame limit.")


def create_error_incident(
    error: BaseException,
    phase: IncidentPhase,
    *,
    severity: IncidentSeverity = IncidentSeverity.FATAL,
    error_traceback: TracebackType | None = None,
    incident_id: UUID | None = None,
    occurred_at_utc: datetime | None = None,
) -> ErrorIncident:
    """Convert an exception into bounded metadata without retaining the exception."""
    traceback_cursor = error_traceback if error_traceback is not None else error.__traceback__
    frames: list[SafeFrame] = []
    while traceback_cursor is not None:
        frame = traceback_cursor.tb_frame
        raw_module = frame.f_globals.get("__name__")
        module_name = raw_module if isinstance(raw_module, str) else "unknown"
        frames.append(
            SafeFrame(
                module_name=_safe_identifier(module_name),
                function_name=_safe_identifier(frame.f_code.co_name),
                filename=_safe_filename(frame.f_code.co_filename),
                line_number=traceback_cursor.tb_lineno,
            )
        )
        traceback_cursor = traceback_cursor.tb_next

    return ErrorIncident(
        incident_id=incident_id or uuid4(),
        occurred_at_utc=occurred_at_utc or datetime.now(UTC),
        phase=phase,
        severity=severity,
        exception_type=_safe_exception_type(type(error).__name__),
        safe_summary=_SAFE_SUMMARIES[phase],
        safe_frames=tuple(frames[-_MAX_FRAMES:]),
    )


def _safe_identifier(value: str) -> str:
    sanitized = _UNSAFE_IDENTIFIER.sub("_", value)[:_MAX_IDENTIFIER_LENGTH]
    return sanitized or "unknown"


def _safe_exception_type(value: str) -> str:
    sanitized = _UNSAFE_IDENTIFIER.sub("_", value)[:_MAX_EXCEPTION_TYPE_LENGTH]
    return sanitized or "Exception"


def _safe_filename(value: str) -> str:
    basename = Path(value).name
    sanitized = _UNSAFE_IDENTIFIER.sub("_", basename)[:_MAX_FILENAME_LENGTH]
    return sanitized or "unknown.py"


def _require_safe_identifier(field_name: str, value: str) -> None:
    if not value or len(value) > _MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"{field_name} must be present and bounded.")
    if _UNSAFE_IDENTIFIER.search(value) is not None:
        raise ValueError(f"{field_name} contains unsafe characters.")


__all__ = [
    "ErrorIncident",
    "IncidentPhase",
    "IncidentSeverity",
    "SafeFrame",
    "create_error_incident",
]
