"""Bounded, read-only collection of privacy-safe support diagnostics."""

from __future__ import annotations

import locale
import os
import platform
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Final

from sqlalchemy.exc import SQLAlchemyError

from app.core import config
from app.core.exceptions import BackupError, DatabaseError, RestoreError
from app.core.redaction import RedactionPolicy
from app.core.settings import Settings
from app.infrastructure.database import DatabaseManager
from app.infrastructure.persistence.alembic_support import (
    create_alembic_config,
    require_single_head,
)
from app.infrastructure.persistence.database_backup import (
    BACKUP_EXTENSION,
    SQLiteBackupService,
)
from app.infrastructure.persistence.restore_metadata import load_operation, load_pending
from app.infrastructure.persistence.restore_workspace import (
    is_link_like,
    optional_startup_restore_paths,
    path_present,
    require_restore_workspace,
)
from app.infrastructure.persistence.sqlite_validation import (
    inspect_sqlite_revision,
    sqlite_file_path,
    validate_current_sqlite_database,
)

_LOG_FILENAME_PATTERN: Final = re.compile(r"^mira-portfolio(?:\.[0-9][0-9T_.:-]*)?\.log$")
_PACKAGE_DISTRIBUTIONS: Final = (
    ("PySide6", "PySide6"),
    ("SQLAlchemy", "SQLAlchemy"),
    ("Alembic", "alembic"),
    ("Pydantic", "pydantic"),
    ("pydantic-settings", "pydantic-settings"),
    ("Loguru", "loguru"),
    ("platformdirs", "platformdirs"),
)
_DIRECTORY_KEYS: Final = (
    "data",
    "cache",
    "database",
    "export",
    "backup",
    "log",
)


@dataclass(frozen=True, slots=True)
class DiagnosticMember:
    """One bounded textual archive member."""

    name: str
    content: bytes
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class DiagnosticCollection:
    """All non-manifest support bundle members."""

    application: dict[str, object]
    system: dict[str, object]
    database: dict[str, object]
    runtime: dict[str, object]
    logs: tuple[DiagnosticMember, ...]


class DiagnosticCollector:
    """Collect sanitized metadata without mutating runtime state."""

    def __init__(
        self,
        settings: Settings,
        database_manager: DatabaseManager,
        policy: RedactionPolicy,
        *,
        log_read_hook: Callable[[Path], None] | None = None,
    ) -> None:
        self._settings = settings
        self._database_manager = database_manager
        self._policy = policy
        self._log_read_hook = log_read_hook

    def collect(self) -> DiagnosticCollection:
        """Collect all bounded diagnostics, degrading optional checks safely."""
        logs, omitted_logs = self._collect_logs()
        return DiagnosticCollection(
            application=self._application_diagnostics(),
            system=self._system_diagnostics(),
            database=self._database_diagnostics(),
            runtime=self._runtime_diagnostics(len(logs), omitted_logs),
            logs=logs,
        )

    def _application_diagnostics(self) -> dict[str, object]:
        settings = self._settings
        return {
            "application_name": self._policy.redact(settings.app_name),
            "application_version": self._policy.redact(settings.app_version),
            "environment": self._policy.redact(settings.environment),
            "debug": settings.debug,
            "theme": self._policy.redact(settings.theme),
            "language": self._policy.redact(settings.language),
            "log_level": self._policy.redact(settings.log_level),
            "default_currency": self._policy.redact(settings.default_currency),
            "auto_backup": settings.auto_backup,
            "auto_snapshot": settings.auto_snapshot,
        }

    def _system_diagnostics(self) -> dict[str, object]:
        return {
            "operating_system_family": platform.system() or "unknown",
            "operating_system_release": self._policy.redact(platform.release() or "unknown"),
            "architecture": platform.machine() or "unknown",
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "packaged": bool(getattr(sys, "frozen", False)),
            "locale": _locale_name(),
            "timezone_offset": _timezone_offset(),
            "package_versions": {
                label: _package_version(distribution)
                for label, distribution in _PACKAGE_DISTRIBUTIONS
            },
        }

    def _database_diagnostics(self) -> dict[str, object]:
        base = _database_defaults()
        _set_expected_head(base, self._settings.database_url)
        try:
            backend = self._database_manager.engine.url.get_backend_name()
        except (DatabaseError, SQLAlchemyError):
            base["status"] = "unavailable"
            base["error_category"] = "manager_unavailable"
            return base

        base["backend_family"] = backend
        base["health_check"] = self._database_manager.health_check()
        if backend != "sqlite":
            base["status"] = "unsupported"
            return base

        base["file_based"] = True
        if not base["health_check"]:
            base["status"] = "error"
            base["error_category"] = "health_check_failed"
        try:
            database_path = sqlite_file_path(self._settings.database_url)
        except ValueError:
            base["file_based"] = False
            base["status"] = "unsupported"
            base["error_category"] = "non_file_database"
            return base

        base["configured_database_filename"] = self._policy.redact(database_path.name)
        exists = path_present(database_path)
        base["database_exists"] = exists
        if exists and not is_link_like(database_path) and database_path.is_file():
            try:
                base["database_size_bytes"] = database_path.stat().st_size
            except OSError:
                base["error_category"] = "filesystem_error"
        else:
            base["status"] = "unavailable"
            base["error_category"] = "database_missing_or_invalid"
            return base

        try:
            current, head = inspect_sqlite_revision(database_path)
            base["current_alembic_revision"] = current
            base["expected_alembic_head"] = head
            base["revision_current"] = current == head
            validate_current_sqlite_database(database_path)
            base["schema_compatible"] = True
        except DatabaseError as error:
            base["schema_compatible"] = False
            base["status"] = "error"
            base["error_category"] = _database_error_category(error)
            _set_expected_head(base, self._settings.database_url)

        try:
            with self._database_manager.engine.connect() as connection:
                integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalars().all()
                foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
                journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            base["sqlite_integrity"] = integrity == ["ok"]
            base["foreign_key_enforcement"] = foreign_keys == 1
            base["journal_mode"] = (
                str(journal_mode).lower()
                if str(journal_mode).lower()
                in {"delete", "truncate", "persist", "memory", "wal", "off"}
                else "unknown"
            )
        except (DatabaseError, SQLAlchemyError):
            base["status"] = "error"
            base["error_category"] = "inspection_error"
            return base

        if base["status"] == "unavailable":
            base["status"] = "ok"
        return base

    def _runtime_diagnostics(
        self,
        included_log_count: int,
        omitted_log_count: int,
    ) -> dict[str, object]:
        paths = (
            self._settings.data_directory,
            self._settings.cache_directory,
            self._settings.database_directory,
            self._settings.export_directory,
            self._settings.backup_directory,
            self._settings.log_directory,
        )
        backups, invalid_backups, backup_scan_truncated = self._backup_counts()
        return {
            "directories": {
                key: _directory_status(path)
                for key, path in zip(_DIRECTORY_KEYS, paths, strict=True)
            },
            "active_log_exists": _regular_non_link(
                self._settings.log_directory / config.LOG_FILENAME
            ),
            "log_file_count": included_log_count,
            "log_omission_count": omitted_log_count,
            "pending_restore_state": self._pending_restore_state(),
            "backup_archive_count": backups,
            "invalid_backup_count": invalid_backups,
            "backup_scan_truncated": backup_scan_truncated,
        }

    def _collect_logs(self) -> tuple[tuple[DiagnosticMember, ...], int]:
        directory = self._settings.log_directory
        if not _regular_directory(directory):
            return (), 0
        try:
            candidates = [
                candidate for candidate in directory.iterdir() if _is_application_log(candidate)
            ]
        except OSError:
            return (), 1
        candidates.sort(key=lambda path: path.name)
        try:
            candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        except OSError:
            return (), len(candidates)

        selected = candidates[: config.SUPPORT_BUNDLE_MAX_LOG_COUNT]
        omitted = len(candidates) - len(selected)
        remaining = config.SUPPORT_BUNDLE_MAX_TOTAL_LOG_BYTES
        members: list[DiagnosticMember] = []
        for index, candidate in enumerate(selected, start=1):
            if remaining <= 0:
                omitted += len(selected) - index + 1
                break
            limit = min(config.SUPPORT_BUNDLE_MAX_LOG_BYTES, remaining)
            try:
                content, truncated = self._read_log_tail(candidate, limit)
            except (OSError, UnicodeError):
                omitted += 1
                continue
            members.append(
                DiagnosticMember(
                    name=f"logs/log-{index}.log",
                    content=content,
                    truncated=truncated,
                )
            )
            remaining -= len(content)
        return tuple(members), omitted

    def _read_log_tail(self, path: Path, maximum_bytes: int) -> tuple[bytes, bool]:
        before = path.lstat()
        if is_link_like(path) or not path.is_file():
            raise OSError("Log entry is not a regular file.")
        if self._log_read_hook is not None:
            self._log_read_hook(path)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                before.st_dev != opened.st_dev
                or before.st_ino != opened.st_ino
                or opened.st_nlink != 1
            ):
                raise OSError("Log entry changed identity while being opened.")
            current_size = opened.st_size
            start = max(0, current_size - maximum_bytes)
            stream.seek(start)
            raw = stream.read(maximum_bytes)
        after = path.lstat()
        if is_link_like(path) or before.st_dev != after.st_dev or before.st_ino != after.st_ino:
            raise OSError("Log entry changed identity while being read.")
        text = raw.decode("utf-8", errors="replace")
        sanitized = self._policy.redact(text, identifiers=True).encode("utf-8")
        if len(sanitized) > maximum_bytes:
            sanitized = (
                sanitized[-maximum_bytes:]
                .decode(
                    "utf-8",
                    errors="ignore",
                )
                .encode("utf-8")
            )
        return sanitized, start > 0 or before.st_size != after.st_size

    def _pending_restore_state(self) -> str:
        paths = optional_startup_restore_paths(self._settings)
        if paths is None or not path_present(paths.workspace):
            return "none"
        try:
            require_restore_workspace(paths.workspace)
            if path_present(paths.operation):
                load_operation(paths)
                if path_present(paths.pending):
                    load_pending(paths)
                return "present"
            if not path_present(paths.pending):
                return "none"
            load_pending(paths)
        except (RestoreError, OSError):
            return "corrupt"
        return "present"

    def _backup_counts(self) -> tuple[int, int, bool]:
        directory = self._settings.backup_directory
        if not path_present(directory):
            return 0, 0, False
        if not _regular_directory(directory):
            return 0, 1, False
        try:
            candidates = sorted(
                (path for path in directory.iterdir() if path.suffix == BACKUP_EXTENSION),
                key=lambda path: path.name,
            )
        except OSError:
            return 0, 1, False
        limit = config.SUPPORT_BUNDLE_MAX_BACKUP_SCAN_COUNT
        selected = candidates[:limit]
        valid = 0
        invalid = 0
        service = SQLiteBackupService(self._settings)
        for path in selected:
            if not _regular_non_link(path):
                invalid += 1
                continue
            try:
                service.verify_backup(path)
            except (BackupError, OSError):
                invalid += 1
            else:
                valid += 1
        return valid, invalid, len(candidates) > limit


def _database_defaults() -> dict[str, object]:
    return {
        "backend_family": "unavailable",
        "file_based": False,
        "configured_database_filename": None,
        "database_exists": False,
        "database_size_bytes": None,
        "health_check": False,
        "current_alembic_revision": None,
        "expected_alembic_head": None,
        "revision_current": False,
        "schema_compatible": None,
        "sqlite_integrity": None,
        "foreign_key_enforcement": None,
        "journal_mode": None,
        "status": "unavailable",
        "error_category": None,
    }


def _set_expected_head(result: dict[str, object], database_url: str) -> None:
    try:
        _, head = require_single_head(create_alembic_config(database_url))
    except DatabaseError:
        return
    result["expected_alembic_head"] = head


def _database_error_category(error: DatabaseError) -> str:
    message = str(error).casefold()
    if "revision" in message:
        return "revision_error"
    if "schema" in message:
        return "schema_error"
    if "integrity" in message:
        return "integrity_error"
    return "validation_error"


def _package_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unavailable"


def _locale_name() -> str:
    try:
        language, encoding = locale.getlocale()
    except Exception:
        return "unavailable"
    if language is None:
        return "unavailable"
    return f"{language}.{encoding}" if encoding else language


def _timezone_offset() -> str:
    offset = datetime.now().astimezone().utcoffset()
    if offset is None:
        return "+00:00"
    seconds = (offset.days * 24 * 60 * 60) + offset.seconds
    sign = "+" if seconds >= 0 else "-"
    minutes = abs(seconds) // 60
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _directory_status(path: Path) -> dict[str, bool]:
    valid = _regular_directory(path)
    return {"exists": valid, "writable": valid and os.access(path, os.W_OK)}


def _regular_directory(path: Path) -> bool:
    return not is_link_like(path) and path.is_dir()


def _regular_non_link(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    return not is_link_like(path) and path.is_file() and details.st_nlink == 1


def _is_application_log(path: Path) -> bool:
    return _LOG_FILENAME_PATTERN.fullmatch(path.name) is not None and _regular_non_link(path)


__all__ = [
    "DiagnosticCollection",
    "DiagnosticCollector",
    "DiagnosticMember",
]
