"""Strict versioned preference resolution and atomic local persistence."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never, cast

from loguru import logger

from app.application.preferences import (
    LogLevelPreference,
    PreferenceField,
    PreferenceLoadStatus,
    PreferenceSaveResult,
    PreferenceSnapshot,
    PreferencesService,
    PreferenceWarningCategory,
    ThemePreference,
    UserPreferences,
)
from app.core import config, runtime_paths
from app.core.exceptions import (
    PreferencePersistenceError,
    PreferenceSecurityError,
    PreferenceValidationError,
)
from app.core.settings import Settings

_APPROVED_FIELDS = frozenset(field.value for field in PreferenceField)
_REPARSE_POINT_ATTRIBUTE = 0x400


@dataclass(frozen=True, slots=True)
class _StoredPreferences:
    theme: ThemePreference | None = None
    auto_backup: bool | None = None
    auto_snapshot: bool | None = None
    log_level: LogLevelPreference | None = None


@dataclass(frozen=True, slots=True)
class _LoadResult:
    stored: _StoredPreferences
    status: PreferenceLoadStatus
    format_version: int | None
    file_exists: bool
    warning_category: PreferenceWarningCategory | None


@dataclass(frozen=True, slots=True)
class PreferenceResolution:
    """Infrastructure result used by bootstrap to preserve one Settings identity."""

    settings: Settings
    service: PreferencesService


class _PreferenceFileStore:
    def __init__(
        self,
        settings: Settings,
        *,
        file_sync: Callable[[int], None] = os.fsync,
        replace: Callable[[Path, Path], None] = os.replace,
        directory_sync: Callable[[Path], None] | None = None,
        post_replace_verify_hook: Callable[[Path], None] | None = None,
    ) -> None:
        self._settings = settings
        self._file_sync = file_sync
        self._replace = replace
        self._directory_sync = directory_sync or _best_effort_directory_sync
        self._post_replace_verify_hook = post_replace_verify_hook

    def load(self) -> _LoadResult:
        data_directory, directory, target = _preference_paths(self._settings)
        try:
            if not _path_present(data_directory):
                return _missing_result()
            _require_safe_directory(data_directory)
            if not _path_present(directory):
                return _missing_result()
            _require_safe_directory(directory)
            if not _path_present(target):
                return _missing_result()
            content = _read_regular_file(target)
            return _parse_preference_content(content)
        except PreferenceSecurityError:
            return _invalid_result(PreferenceWarningCategory.SECURITY, file_exists=True)
        except PreferenceValidationError:
            return _invalid_result(PreferenceWarningCategory.INVALID_FORMAT, file_exists=True)
        except OSError:
            return _invalid_result(PreferenceWarningCategory.READ_ERROR, file_exists=True)

    def save(self, stored: _StoredPreferences) -> None:
        content = _serialize_preferences(stored)
        expected = _parse_preference_content(content)
        if expected.status is not PreferenceLoadStatus.LOADED or expected.stored != stored:
            raise PreferenceValidationError("The proposed preference document is invalid.")

        data_directory, directory, target = _preference_paths(self._settings)
        _require_safe_directory(data_directory)
        _ensure_preference_directory(directory)
        existing = _read_regular_file(target) if _path_present(target) else None

        temporary: Path | None = None
        rollback: Path | None = None
        try:
            temporary = self._write_sibling(directory, content, ".preferences-")
            _verify_stored_file(temporary, stored)
            if existing is not None:
                rollback = self._write_sibling(directory, existing, ".preferences-rollback-")
            self._replace(temporary, target)
            temporary = None
            try:
                if self._post_replace_verify_hook is not None:
                    self._post_replace_verify_hook(target)
                _verify_stored_file(target, stored)
            except Exception as error:
                self._restore_previous(target, rollback, existing is not None)
                rollback = None
                raise PreferencePersistenceError(
                    "The installed preference file could not be verified."
                ) from error
            self._sync_directory(directory)
        except (PreferencePersistenceError, PreferenceSecurityError, PreferenceValidationError):
            raise
        except OSError as error:
            raise PreferencePersistenceError(
                "Preferences could not be installed atomically."
            ) from error
        finally:
            _best_effort_unlink(temporary)
            _best_effort_unlink(rollback)

    def reset(self) -> None:
        data_directory, directory, target = _preference_paths(self._settings)
        if not _path_present(data_directory) or not _path_present(directory):
            return
        _require_safe_directory(data_directory)
        _require_safe_directory(directory)
        if not _path_present(target):
            return
        _require_safe_regular_file(target)
        try:
            target.unlink()
            self._sync_directory(directory)
        except OSError as error:
            raise PreferencePersistenceError("Preferences could not be reset safely.") from error

    def _write_sibling(self, directory: Path, content: bytes, prefix: str) -> Path:
        descriptor = -1
        path: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=prefix,
                suffix=".tmp",
                dir=directory,
            )
            path = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                self._file_sync(stream.fileno())
            return path
        except OSError as error:
            _best_effort_unlink(path)
            raise PreferencePersistenceError(
                "A durable temporary preference file could not be written."
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _restore_previous(
        self,
        target: Path,
        rollback: Path | None,
        had_existing: bool,
    ) -> None:
        try:
            if had_existing:
                if rollback is None:
                    raise OSError("Rollback state is unavailable.")
                self._replace(rollback, target)
            else:
                target.unlink(missing_ok=True)
            self._sync_directory(target.parent)
        except OSError as error:
            raise PreferencePersistenceError(
                "The previous preference file could not be restored."
            ) from error

    def _sync_directory(self, directory: Path) -> None:
        try:
            self._directory_sync(directory)
        except OSError:
            return


class FilePreferencesService(PreferencesService):
    """Resolve and explicitly persist preferences without mutating running Settings."""

    def __init__(
        self,
        base_settings: Settings,
        running_settings: Settings,
        initial_snapshot: PreferenceSnapshot,
        store: _PreferenceFileStore,
        overridden_fields: frozenset[PreferenceField],
    ) -> None:
        self._base_settings = base_settings
        self._settings = running_settings
        self._running_preferences = _preferences_from_settings(running_settings)
        self._current = initial_snapshot
        self._store = store
        self._overridden_fields = overridden_fields
        self._lock = threading.Lock()

    def get_current(self) -> PreferenceSnapshot:
        with self._lock:
            return self._current

    def save(self, preferences: UserPreferences) -> PreferenceSaveResult:
        if not isinstance(preferences, UserPreferences):
            raise PreferenceValidationError("A complete UserPreferences value is required.")
        stored = _stored_for_save(preferences, self._overridden_fields)
        with self._lock:
            self._store.save(stored)
            effective = _resolve_effective_preferences(
                self._base_settings,
                stored,
                self._overridden_fields,
            )
            restart_fields = _changed_fields(self._running_preferences, effective)
            self._current = PreferenceSnapshot(
                preferences=effective,
                status=PreferenceLoadStatus.LOADED,
                format_version=config.PREFERENCES_FORMAT_VERSION,
                preference_file_exists=True,
                restart_required_fields=restart_fields,
                overridden_by_environment=self._overridden_fields,
                warning_category=None,
            )
        saved_at = datetime.now(UTC)
        logger.info(
            "Preference save completed; restart fields: {}; environment overrides: {}",
            len(restart_fields),
            len(self._overridden_fields),
        )
        return PreferenceSaveResult(
            preferences=effective,
            saved_at_utc=saved_at,
            restart_required_fields=restart_fields,
            overridden_by_environment=self._overridden_fields,
        )

    def reset(self) -> PreferenceSnapshot:
        with self._lock:
            self._store.reset()
            effective = _resolve_effective_preferences(
                self._base_settings,
                _StoredPreferences(),
                self._overridden_fields,
            )
            self._current = PreferenceSnapshot(
                preferences=effective,
                status=PreferenceLoadStatus.MISSING,
                format_version=None,
                preference_file_exists=False,
                restart_required_fields=_changed_fields(
                    self._running_preferences,
                    effective,
                ),
                overridden_by_environment=self._overridden_fields,
                warning_category=None,
            )
            snapshot = self._current
        logger.info(
            "Preference reset completed; environment overrides: {}",
            len(self._overridden_fields),
        )
        return snapshot

    def reload(self) -> PreferenceSnapshot:
        with self._lock:
            loaded = self._store.load()
            effective = _resolve_effective_preferences(
                self._base_settings,
                loaded.stored,
                self._overridden_fields,
            )
            self._current = _snapshot_from_load(
                loaded,
                effective,
                self._overridden_fields,
                _changed_fields(self._running_preferences, effective),
            )
            return self._current


def resolve_preferences(
    base_settings: Settings,
    *,
    file_sync: Callable[[int], None] = os.fsync,
    replace: Callable[[Path, Path], None] = os.replace,
    directory_sync: Callable[[Path], None] | None = None,
    post_replace_verify_hook: Callable[[Path], None] | None = None,
) -> PreferenceResolution:
    """Load preferences and build one new effective Settings instance."""
    store = _PreferenceFileStore(
        base_settings,
        file_sync=file_sync,
        replace=replace,
        directory_sync=directory_sync,
        post_replace_verify_hook=post_replace_verify_hook,
    )
    loaded = store.load()
    overrides = _environment_overrides(base_settings)
    effective = _resolve_effective_preferences(base_settings, loaded.stored, overrides)
    resolved_settings = _rebuild_settings(base_settings, effective)
    snapshot = _snapshot_from_load(loaded, effective, overrides, frozenset())
    service = FilePreferencesService(
        base_settings,
        resolved_settings,
        snapshot,
        store,
        overrides,
    )
    return PreferenceResolution(resolved_settings, service)


def create_unloaded_preferences_service(settings: Settings) -> PreferencesService:
    """Create a no-I/O compatibility service for non-bootstrap composition callers."""
    preferences = _preferences_from_settings(settings)
    overrides = _environment_overrides(settings)
    snapshot = PreferenceSnapshot(
        preferences=preferences,
        status=PreferenceLoadStatus.MISSING,
        format_version=None,
        preference_file_exists=False,
        restart_required_fields=frozenset(),
        overridden_by_environment=overrides,
        warning_category=None,
    )
    return FilePreferencesService(
        settings,
        settings,
        snapshot,
        _PreferenceFileStore(settings),
        overrides,
    )


def _preferences_from_settings(settings: Settings) -> UserPreferences:
    try:
        return UserPreferences(
            theme=ThemePreference(settings.theme),
            auto_backup=settings.auto_backup,
            auto_snapshot=settings.auto_snapshot,
            log_level=LogLevelPreference(settings.log_level),
        )
    except (TypeError, ValueError) as error:
        raise PreferenceValidationError(
            "An explicitly configured preference value is unsupported."
        ) from error


def _environment_overrides(settings: Settings) -> frozenset[PreferenceField]:
    explicitly_set = settings.model_fields_set
    return frozenset(field for field in PreferenceField if field.value in explicitly_set)


def _resolve_effective_preferences(
    base_settings: Settings,
    stored: _StoredPreferences,
    overrides: frozenset[PreferenceField],
) -> UserPreferences:
    base = _preferences_from_settings(base_settings)
    return UserPreferences(
        theme=(
            base.theme
            if PreferenceField.THEME in overrides or stored.theme is None
            else stored.theme
        ),
        auto_backup=(
            base.auto_backup
            if PreferenceField.AUTO_BACKUP in overrides or stored.auto_backup is None
            else stored.auto_backup
        ),
        auto_snapshot=(
            base.auto_snapshot
            if PreferenceField.AUTO_SNAPSHOT in overrides or stored.auto_snapshot is None
            else stored.auto_snapshot
        ),
        log_level=(
            base.log_level
            if PreferenceField.LOG_LEVEL in overrides or stored.log_level is None
            else stored.log_level
        ),
    )


def _rebuild_settings(base: Settings, preferences: UserPreferences) -> Settings:
    values = base.model_dump()
    values.update(
        {
            "theme": preferences.theme.value,
            "auto_backup": preferences.auto_backup,
            "auto_snapshot": preferences.auto_snapshot,
            "log_level": preferences.log_level.value,
        }
    )
    validated = Settings.model_validate(values)
    changed_values: dict[str, object] = {
        field_name: getattr(validated, field_name)
        for field_name in _APPROVED_FIELDS
        if getattr(validated, field_name) != getattr(base, field_name)
    }
    return base.model_copy(update=changed_values, deep=True)


def _snapshot_from_load(
    loaded: _LoadResult,
    effective: UserPreferences,
    overrides: frozenset[PreferenceField],
    restart_fields: frozenset[PreferenceField],
) -> PreferenceSnapshot:
    return PreferenceSnapshot(
        preferences=effective,
        status=loaded.status,
        format_version=loaded.format_version,
        preference_file_exists=loaded.file_exists,
        restart_required_fields=restart_fields,
        overridden_by_environment=overrides,
        warning_category=loaded.warning_category,
    )


def _stored_for_save(
    preferences: UserPreferences,
    overrides: frozenset[PreferenceField],
) -> _StoredPreferences:
    return _StoredPreferences(
        theme=None if PreferenceField.THEME in overrides else preferences.theme,
        auto_backup=(None if PreferenceField.AUTO_BACKUP in overrides else preferences.auto_backup),
        auto_snapshot=(
            None if PreferenceField.AUTO_SNAPSHOT in overrides else preferences.auto_snapshot
        ),
        log_level=(None if PreferenceField.LOG_LEVEL in overrides else preferences.log_level),
    )


def _changed_fields(
    running: UserPreferences,
    proposed: UserPreferences,
) -> frozenset[PreferenceField]:
    changed: set[PreferenceField] = set()
    if running.theme is not proposed.theme:
        changed.add(PreferenceField.THEME)
    if running.auto_backup is not proposed.auto_backup:
        changed.add(PreferenceField.AUTO_BACKUP)
    if running.auto_snapshot is not proposed.auto_snapshot:
        changed.add(PreferenceField.AUTO_SNAPSHOT)
    if running.log_level is not proposed.log_level:
        changed.add(PreferenceField.LOG_LEVEL)
    return frozenset(changed)


def _parse_preference_content(content: bytes) -> _LoadResult:
    if len(content) > config.PREFERENCES_MAX_FILE_BYTES:
        raise PreferenceValidationError("The preference file exceeds its size limit.")
    try:
        decoded = content.decode("utf-8", errors="strict")
        parsed = cast(
            object,
            json.loads(
                decoded,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            ),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise PreferenceValidationError("The preference file is not valid JSON.") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise PreferenceValidationError("The preference file must contain one object.")
    if set(parsed) != {"format_version", "preferences"}:
        raise PreferenceValidationError("The preference document fields are invalid.")
    version = parsed.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise PreferenceValidationError("The preference format version is invalid.")
    if version != config.PREFERENCES_FORMAT_VERSION:
        return _LoadResult(
            stored=_StoredPreferences(),
            status=PreferenceLoadStatus.UNSUPPORTED_VERSION,
            format_version=version,
            file_exists=True,
            warning_category=PreferenceWarningCategory.UNSUPPORTED_VERSION,
        )
    values = parsed.get("preferences")
    if not isinstance(values, dict) or not all(isinstance(key, str) for key in values):
        raise PreferenceValidationError("The preference values must contain one object.")
    if not set(values).issubset(_APPROVED_FIELDS):
        raise PreferenceValidationError("The preference document contains unknown fields.")
    theme = _optional_enum(values, PreferenceField.THEME.value, ThemePreference)
    log_level = _optional_enum(
        values,
        PreferenceField.LOG_LEVEL.value,
        LogLevelPreference,
    )
    auto_backup = _optional_boolean(values, PreferenceField.AUTO_BACKUP.value)
    auto_snapshot = _optional_boolean(values, PreferenceField.AUTO_SNAPSHOT.value)
    return _LoadResult(
        stored=_StoredPreferences(theme, auto_backup, auto_snapshot, log_level),
        status=PreferenceLoadStatus.LOADED,
        format_version=version,
        file_exists=True,
        warning_category=None,
    )


def _serialize_preferences(stored: _StoredPreferences) -> bytes:
    values: dict[str, str | bool] = {}
    if stored.theme is not None:
        values[PreferenceField.THEME.value] = stored.theme.value
    if stored.auto_backup is not None:
        values[PreferenceField.AUTO_BACKUP.value] = stored.auto_backup
    if stored.auto_snapshot is not None:
        values[PreferenceField.AUTO_SNAPSHOT.value] = stored.auto_snapshot
    if stored.log_level is not None:
        values[PreferenceField.LOG_LEVEL.value] = stored.log_level.value
    document: dict[str, object] = {
        "format_version": config.PREFERENCES_FORMAT_VERSION,
        "preferences": values,
    }
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _optional_enum[PreferenceEnum: (ThemePreference, LogLevelPreference)](
    values: dict[object, object],
    key: str,
    enum_type: type[PreferenceEnum],
) -> PreferenceEnum | None:
    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PreferenceValidationError("A string preference value is invalid.")
    try:
        return enum_type(value)
    except ValueError as error:
        raise PreferenceValidationError("A string preference value is unsupported.") from error


def _optional_boolean(values: dict[object, object], key: str) -> bool | None:
    value = values.get(key)
    if value is None:
        return None
    if type(value) is not bool:
        raise PreferenceValidationError("A boolean preference value is invalid.")
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> Never:
    raise ValueError("Unsupported JSON constant.")


def _preference_paths(settings: Settings) -> tuple[Path, Path, Path]:
    data_directory = Path(os.path.abspath(settings.data_directory))
    directory = Path(os.path.abspath(runtime_paths.preferences_directory(data_directory)))
    target = Path(os.path.abspath(runtime_paths.preferences_file(data_directory)))
    if (
        directory.parent != data_directory
        or target.parent != directory
        or directory.name != config.PREFERENCES_DIRECTORY_NAME
        or target.name != config.PREFERENCES_FILENAME
    ):
        raise PreferenceSecurityError("The preference location is outside its approved directory.")
    return data_directory, directory, target


def _read_regular_file(path: Path) -> bytes:
    _require_safe_regular_file(path)
    details = path.lstat()
    if details.st_size > config.PREFERENCES_MAX_FILE_BYTES:
        raise PreferenceValidationError("The preference file exceeds its size limit.")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (
            details.st_dev != opened.st_dev
            or details.st_ino != opened.st_ino
            or opened.st_nlink != 1
        ):
            raise PreferenceSecurityError("The preference file changed while being opened.")
        content = stream.read(config.PREFERENCES_MAX_FILE_BYTES + 1)
    if len(content) > config.PREFERENCES_MAX_FILE_BYTES:
        raise PreferenceValidationError("The preference file exceeds its size limit.")
    return content


def _verify_stored_file(path: Path, expected: _StoredPreferences) -> None:
    loaded = _parse_preference_content(_read_regular_file(path))
    if loaded.status is not PreferenceLoadStatus.LOADED or loaded.stored != expected:
        raise PreferenceValidationError("The preference file verification failed.")


def _ensure_preference_directory(directory: Path) -> None:
    try:
        if _path_present(directory):
            _require_safe_directory(directory)
            return
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        _require_safe_directory(directory)
    except (PreferenceSecurityError, PreferencePersistenceError):
        raise
    except OSError as error:
        raise PreferencePersistenceError(
            "The preference directory could not be created."
        ) from error


def _require_safe_directory(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise PreferenceSecurityError("The preference directory is unavailable.") from error
    if _is_link_like(details) or not stat.S_ISDIR(details.st_mode):
        raise PreferenceSecurityError("The preference directory is unsafe.")


def _require_safe_regular_file(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise PreferenceSecurityError("The preference file is unavailable.") from error
    if _is_link_like(details) or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise PreferenceSecurityError("The preference file is unsafe.")


def _is_link_like(details: os.stat_result) -> bool:
    attributes = cast(int, getattr(details, "st_file_attributes", 0))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _path_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _missing_result() -> _LoadResult:
    return _LoadResult(
        stored=_StoredPreferences(),
        status=PreferenceLoadStatus.MISSING,
        format_version=None,
        file_exists=False,
        warning_category=None,
    )


def _invalid_result(
    warning: PreferenceWarningCategory,
    *,
    file_exists: bool,
) -> _LoadResult:
    return _LoadResult(
        stored=_StoredPreferences(),
        status=PreferenceLoadStatus.INVALID,
        format_version=None,
        file_exists=file_exists,
        warning_category=warning,
    )


def _best_effort_directory_sync(directory: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _best_effort_unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


__all__ = [
    "PreferenceResolution",
    "create_unloaded_preferences_service",
    "resolve_preferences",
]
