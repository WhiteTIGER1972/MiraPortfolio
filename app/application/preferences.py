"""Application-facing contracts and immutable DTOs for user preferences."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PreferenceField(StrEnum):
    """Approved user-editable Settings fields."""

    THEME = "theme"
    AUTO_BACKUP = "auto_backup"
    AUTO_SNAPSHOT = "auto_snapshot"
    LOG_LEVEL = "log_level"


class ThemePreference(StrEnum):
    """Themes backed by an implemented application palette."""

    DARK = "dark"


class LogLevelPreference(StrEnum):
    """Supported persistent Loguru threshold names."""

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class PreferenceLoadStatus(StrEnum):
    """Outcome of loading the versioned preference file."""

    MISSING = "missing"
    LOADED = "loaded"
    INVALID = "invalid"
    UNSUPPORTED_VERSION = "unsupported_version"


class PreferenceWarningCategory(StrEnum):
    """Sanitized reason a preference file was not applied."""

    INVALID_FORMAT = "invalid_format"
    READ_ERROR = "read_error"
    SECURITY = "security"
    UNSUPPORTED_VERSION = "unsupported_version"


@dataclass(frozen=True, slots=True)
class UserPreferences:
    """Complete effective values for the approved preference scope."""

    theme: ThemePreference
    auto_backup: bool
    auto_snapshot: bool
    log_level: LogLevelPreference

    def __post_init__(self) -> None:
        if not isinstance(self.theme, ThemePreference):
            raise TypeError("theme must be a supported ThemePreference.")
        if type(self.auto_backup) is not bool:
            raise TypeError("auto_backup must be a strict boolean.")
        if type(self.auto_snapshot) is not bool:
            raise TypeError("auto_snapshot must be a strict boolean.")
        if not isinstance(self.log_level, LogLevelPreference):
            raise TypeError("log_level must be a supported LogLevelPreference.")


@dataclass(frozen=True, slots=True)
class PreferenceSnapshot:
    """Effective preferences plus safe load and precedence metadata."""

    preferences: UserPreferences
    status: PreferenceLoadStatus
    format_version: int | None
    preference_file_exists: bool
    restart_required_fields: frozenset[PreferenceField]
    overridden_by_environment: frozenset[PreferenceField]
    warning_category: PreferenceWarningCategory | None


@dataclass(frozen=True, slots=True)
class PreferenceSaveResult:
    """Describe one verified preference save without mutating running Settings."""

    preferences: UserPreferences
    saved_at_utc: datetime
    restart_required_fields: frozenset[PreferenceField]
    overridden_by_environment: frozenset[PreferenceField]


class PreferencesService(ABC):
    """Read and explicitly persist the approved user preference set."""

    @abstractmethod
    def get_current(self) -> PreferenceSnapshot:
        """Return the latest immutable preference snapshot."""

    @abstractmethod
    def save(self, preferences: UserPreferences) -> PreferenceSaveResult:
        """Persist a complete proposed preference set for a later startup."""

    @abstractmethod
    def reset(self) -> PreferenceSnapshot:
        """Remove only the application-owned preference file."""

    @abstractmethod
    def reload(self) -> PreferenceSnapshot:
        """Reload persisted state without mutating the running Settings instance."""


__all__ = [
    "LogLevelPreference",
    "PreferenceField",
    "PreferenceLoadStatus",
    "PreferenceSaveResult",
    "PreferenceSnapshot",
    "PreferenceWarningCategory",
    "PreferencesService",
    "ThemePreference",
    "UserPreferences",
]
