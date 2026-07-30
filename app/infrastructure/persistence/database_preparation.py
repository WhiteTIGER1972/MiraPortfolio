"""Release-safe database migration, import, and validation lifecycle."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from app.core import runtime_paths
from app.core.exceptions import DatabaseError
from app.core.settings import Settings
from app.infrastructure.persistence.alembic_support import (
    create_alembic_config,
    require_single_head,
    run_stamp,
    run_upgrade,
    validate_schema,
    validated_current_revision,
    verify_revision,
)
from app.infrastructure.persistence.sqlite_validation import (
    open_read_only_sqlite,
    sqlite_file_path,
    verify_sqlite_integrity,
)

ALEMBIC_VERSION_TABLE: Final = "alembic_version"
LEGACY_DATABASE_FILENAMES: Final = ("portfolio.db", "mira_portfolio.db")
SQLITE_SIDECAR_SUFFIXES: Final = ("-wal", "-shm", "-journal")


class PreparationOutcome(StrEnum):
    """Describe the durable database action completed during preparation."""

    CREATED = "created"
    IMPORTED_LEGACY = "imported_legacy"
    ALREADY_CURRENT = "already_current"
    UPGRADED = "upgraded"
    STAMPED_LEGACY = "stamped_legacy"


@dataclass(frozen=True, slots=True)
class DatabasePreparationResult:
    """Return the non-sensitive outcome needed by startup logging and tests."""

    outcome: PreparationOutcome


@dataclass(frozen=True, slots=True)
class _FileFingerprint:
    size: int
    modified_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _LegacyCandidate:
    path: Path
    fingerprint: _FileFingerprint


def prepare_database(
    settings: Settings,
    *,
    legacy_search_directory: Path | None = None,
    script_location: Path | None = None,
) -> DatabasePreparationResult:
    """Prepare the configured database before the long-lived runtime engine exists."""
    try:
        configured_url = make_url(settings.database_url)
    except (TypeError, ValueError, SQLAlchemyError) as error:
        raise DatabaseError("The configured database URL is invalid.") from error

    if configured_url.get_backend_name() != "sqlite":
        outcome = _prepare_non_sqlite(settings.database_url, script_location)
        return DatabasePreparationResult(outcome)

    try:
        target = sqlite_file_path(settings.database_url)
    except ValueError as error:
        raise DatabaseError("The configured SQLite database must use a file path.") from error
    if not target.parent.is_dir():
        raise DatabaseError(f"Database directory does not exist: '{target.parent}'.")
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise DatabaseError(f"Database target is not a regular file: '{target}'.")
    if not target.exists() and any(path.exists() for path in _sidecar_paths(target)):
        raise DatabaseError(f"Orphaned SQLite sidecar files exist for '{target}'.")

    legacy_source: _LegacyCandidate | None = None
    if not target.exists() and _legacy_discovery_is_enabled(settings):
        search_directory = (legacy_search_directory or Path.cwd()).resolve()
        legacy_source = _discover_legacy_database(search_directory, target)

    outcome = _prepare_sqlite_file(
        configured_url=configured_url,
        target=target,
        legacy_source=legacy_source,
        script_location=script_location,
    )
    if legacy_source is not None:
        outcome = PreparationOutcome.IMPORTED_LEGACY
    return DatabasePreparationResult(outcome)


def _prepare_non_sqlite(
    database_url: str,
    script_location: Path | None,
) -> PreparationOutcome:
    config = create_alembic_config(database_url, script_location=script_location)
    script, head = require_single_head(config)
    engine: Engine | None = None
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            previous = validated_current_revision(connection, script)
            run_upgrade(config, connection)
            verify_revision(connection, head)
            validate_schema(connection)
        if previous == head:
            return PreparationOutcome.ALREADY_CURRENT
        return PreparationOutcome.UPGRADED
    except DatabaseError:
        raise
    except Exception as error:
        raise DatabaseError("Database migration and validation failed.") from error
    finally:
        if engine is not None:
            engine.dispose()


def _legacy_discovery_is_enabled(settings: Settings) -> bool:
    default_url = runtime_paths.sqlite_url_for_path(settings.database_path)
    return settings.database_url == default_url and "database_url" not in settings.model_fields_set


def _discover_legacy_database(search_directory: Path, target: Path) -> _LegacyCandidate | None:
    if not search_directory.is_dir():
        raise DatabaseError(f"Legacy database search directory is invalid: '{search_directory}'.")

    normalized_target = os.path.normcase(str(target.resolve()))
    eligible: list[_LegacyCandidate] = []
    for filename in LEGACY_DATABASE_FILENAMES:
        candidate = search_directory / filename
        if os.path.normcase(str(candidate.resolve())) == normalized_target:
            continue
        if candidate.is_symlink():
            raise DatabaseError(f"Legacy database candidate is not a regular file: '{candidate}'.")
        if not candidate.exists():
            continue
        if not candidate.is_file():
            raise DatabaseError(f"Legacy database candidate is not a regular file: '{candidate}'.")
        if candidate.stat().st_size == 0:
            continue

        fingerprint = _fingerprint(candidate)
        try:
            verify_sqlite_integrity(candidate)
        except DatabaseError as error:
            raise DatabaseError(f"Legacy database candidate is invalid: '{candidate}'.") from error
        if _fingerprint(candidate) != fingerprint:
            raise DatabaseError(f"Legacy database changed while it was inspected: '{candidate}'.")
        eligible.append(_LegacyCandidate(candidate.resolve(), fingerprint))

    if len(eligible) > 1:
        raise DatabaseError(
            "Multiple valid legacy databases were found; remove the ambiguity before starting."
        )
    return eligible[0] if eligible else None


def _prepare_sqlite_file(
    *,
    configured_url: URL,
    target: Path,
    legacy_source: _LegacyCandidate | None,
    script_location: Path | None,
) -> PreparationOutcome:
    target_existed = target.exists()
    source = (
        legacy_source.path if legacy_source is not None else (target if target_existed else None)
    )
    staging = target.with_name(f".{target.name}.{uuid4().hex}.staging")
    staging_url = configured_url.set(database=str(staging)).render_as_string(hide_password=False)
    config = create_alembic_config(staging_url, script_location=script_location)

    try:
        _, head = require_single_head(config)
        if source is not None:
            source_fingerprint = (
                legacy_source.fingerprint if legacy_source is not None else _fingerprint(source)
            )
            _backup_sqlite(source, staging)
            if _fingerprint(source) != source_fingerprint:
                raise DatabaseError(f"Source database changed while it was copied: '{source}'.")

        outcome = _prepare_sqlite_staging(staging_url, config, head)
        _cleanup_sqlite_sidecars(staging)
        _atomic_install(staging, target, target_existed=target_existed)
        return outcome
    except DatabaseError:
        raise
    except Exception as error:
        raise DatabaseError(f"Database preparation failed for '{target}'.") from error
    finally:
        _cleanup_sqlite_files(staging)


def _backup_sqlite(source: Path, destination: Path) -> None:
    try:
        with closing(open_read_only_sqlite(source)) as source_connection:
            with closing(sqlite3.connect(destination)) as destination_connection:
                source_connection.backup(destination_connection)
                destination_connection.commit()
    except sqlite3.Error as error:
        raise DatabaseError(f"Database copy failed for '{source}'.") from error


def _prepare_sqlite_staging(
    database_url: str,
    config: Config,
    head: str,
) -> PreparationOutcome:
    engine: Engine | None = None
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            outcome = _prepare_connection(config, connection, head)
            verify_revision(connection, head)
            validate_schema(connection)
        engine.dispose()
        engine = None
        verify_sqlite_integrity(sqlite_file_path(database_url), verify_foreign_keys=True)
        return outcome
    except DatabaseError:
        raise
    except Exception as error:
        raise DatabaseError("Staging database migration and validation failed.") from error
    finally:
        if engine is not None:
            engine.dispose()


def _prepare_connection(
    config: Config,
    connection: Connection,
    head: str,
) -> PreparationOutcome:
    script = ScriptDirectory.from_config(config)
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    user_tables = table_names - {ALEMBIC_VERSION_TABLE}
    has_version_table = ALEMBIC_VERSION_TABLE in table_names
    current = validated_current_revision(connection, script) if has_version_table else None

    if current is not None:
        run_upgrade(config, connection)
        if current == head:
            return PreparationOutcome.ALREADY_CURRENT
        return PreparationOutcome.UPGRADED

    if user_tables:
        validate_schema(connection)
        run_stamp(config, connection, head)
        return PreparationOutcome.STAMPED_LEGACY

    run_upgrade(config, connection)
    return PreparationOutcome.CREATED


def _fingerprint(path: Path) -> _FileFingerprint:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return _FileFingerprint(stat.st_size, stat.st_mtime_ns, digest.hexdigest())


def _atomic_install(staging: Path, target: Path, *, target_existed: bool) -> None:
    if not target_existed:
        os.replace(staging, target)
        return

    rollback = target.with_name(f".{target.name}.{uuid4().hex}.rollback")
    saved_sidecars: list[tuple[Path, Path]] = []
    shutil.copy2(target, rollback)
    try:
        for suffix in SQLITE_SIDECAR_SUFFIXES:
            sidecar = Path(f"{target}{suffix}")
            if sidecar.exists():
                saved = Path(f"{rollback}{suffix}")
                os.replace(sidecar, saved)
                saved_sidecars.append((sidecar, saved))
        os.replace(staging, target)
    except Exception:
        if rollback.exists():
            os.replace(rollback, target)
        for sidecar, saved in saved_sidecars:
            if saved.exists():
                os.replace(saved, sidecar)
        raise
    else:
        _cleanup_sqlite_files(rollback)


def _cleanup_sqlite_files(database: Path) -> None:
    for path in (database, *_sidecar_paths(database)):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise DatabaseError("Temporary database files could not be removed.") from error


def _cleanup_sqlite_sidecars(database: Path) -> None:
    for path in _sidecar_paths(database):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise DatabaseError("Temporary SQLite sidecars could not be removed.") from error


def _sidecar_paths(database: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{database}{suffix}") for suffix in SQLITE_SIDECAR_SUFFIXES)


__all__ = [
    "DatabasePreparationResult",
    "PreparationOutcome",
    "prepare_database",
]
