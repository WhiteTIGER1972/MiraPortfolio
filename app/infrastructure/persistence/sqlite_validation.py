"""Read-only SQLite path, fingerprint, integrity, revision, and schema validation."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.core import runtime_paths
from app.core.exceptions import DatabaseError
from app.infrastructure.persistence.alembic_support import (
    create_alembic_config,
    require_single_head,
    validate_schema,
    validated_current_revision,
    verify_revision,
)

HASH_CHUNK_SIZE: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """Record one file's presence, type, metadata, and content hash."""

    exists: bool
    regular_file: bool
    size: int | None
    modified_ns: int | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class SQLiteDatabaseFingerprint:
    """Record the main SQLite file and every relevant sidecar."""

    database: FileFingerprint
    wal: FileFingerprint
    shm: FileFingerprint
    journal: FileFingerprint


def sqlite_file_path(database_url: str) -> Path:
    """Resolve one unambiguous file-based SQLite URL without opening it."""
    try:
        configured_url = make_url(database_url)
    except Exception as error:
        raise ValueError("The configured database URL is invalid.") from error
    if configured_url.get_backend_name() != "sqlite":
        raise ValueError("Backups support only file-based SQLite databases.")
    database = configured_url.database
    if database in {None, "", ":memory:"} or database.startswith("file:"):
        raise ValueError("Backups require an unambiguous SQLite file path.")
    path = Path(database)
    if not path.is_absolute():
        path = Path.cwd() / path
    return Path(os.path.abspath(path))


def open_read_only_sqlite(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite file with URI read-only and query-only protection."""
    absolute_path = Path(os.path.abspath(path))
    connection = sqlite3.connect(f"{absolute_path.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def open_sqlite_backup_source(path: Path) -> sqlite3.Connection:
    """Open a backup source without creating sidecars when none are present."""
    absolute_path = Path(os.path.abspath(path))
    sidecars_present = any(sidecar.exists() for sidecar in sqlite_sidecar_paths(absolute_path))
    query = "mode=ro" if sidecars_present else "mode=ro&immutable=1"
    connection = sqlite3.connect(f"{absolute_path.as_uri()}?{query}", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def verify_sqlite_integrity(path: Path, *, verify_foreign_keys: bool = False) -> None:
    """Verify SQLite integrity, optionally checking connection-level FK enforcement."""
    try:
        with closing(open_read_only_sqlite(path)) as connection:
            if verify_foreign_keys:
                connection.execute("PRAGMA foreign_keys = ON")
                if connection.execute("PRAGMA foreign_keys").fetchall() != [(1,)]:
                    raise DatabaseError("SQLite foreign key enforcement could not be enabled.")
            result = connection.execute("PRAGMA integrity_check").fetchall()
    except DatabaseError:
        raise
    except sqlite3.Error as error:
        raise DatabaseError(f"SQLite integrity validation failed for '{path}'.") from error
    if result != [("ok",)]:
        raise DatabaseError(f"SQLite integrity validation failed for '{path}'.")


def validate_current_sqlite_database(
    path: Path,
    *,
    script_location: Path | None = None,
) -> str:
    """Validate integrity, current revision, and ORM schema without modifying the file."""
    engine: Engine | None = None
    try:
        verify_sqlite_integrity(path)
        database_url = runtime_paths.sqlite_url_for_path(path)
        config = create_alembic_config(database_url, script_location=script_location)
        script, head = require_single_head(config)
        engine = create_engine(
            "sqlite+pysqlite://",
            creator=lambda: open_read_only_sqlite(path),
            poolclass=NullPool,
        )
        with engine.connect() as connection:
            current = validated_current_revision(connection, script)
            if current is None:
                raise DatabaseError("The database has no Alembic revision.")
            verify_revision(connection, head)
            validate_schema(connection)
        return head
    except DatabaseError:
        raise
    except (OSError, sqlite3.Error, SQLAlchemyError) as error:
        raise DatabaseError(f"SQLite database validation failed for '{path}'.") from error
    finally:
        if engine is not None:
            engine.dispose()


def inspect_sqlite_revision(
    path: Path,
    *,
    script_location: Path | None = None,
) -> tuple[str | None, str]:
    """Return a known current revision and sole head without modifying the database."""
    engine: Engine | None = None
    try:
        verify_sqlite_integrity(path)
        database_url = runtime_paths.sqlite_url_for_path(path)
        config = create_alembic_config(database_url, script_location=script_location)
        script, head = require_single_head(config)
        engine = create_engine(
            "sqlite+pysqlite://",
            creator=lambda: open_read_only_sqlite(path),
            poolclass=NullPool,
        )
        with engine.connect() as connection:
            return validated_current_revision(connection, script), head
    except DatabaseError:
        raise
    except (OSError, sqlite3.Error, SQLAlchemyError) as error:
        raise DatabaseError(f"SQLite revision inspection failed for '{path}'.") from error
    finally:
        if engine is not None:
            engine.dispose()


def fingerprint_file(path: Path) -> FileFingerprint:
    """Fingerprint a path without interpreting its contents."""
    if path.is_symlink():
        return FileFingerprint(True, False, None, None, None)
    if not path.exists():
        return FileFingerprint(False, False, None, None, None)
    if not path.is_file():
        return FileFingerprint(True, False, None, None, None)
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(HASH_CHUNK_SIZE), b""):
            digest.update(block)
    return FileFingerprint(
        exists=True,
        regular_file=True,
        size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        sha256=digest.hexdigest(),
    )


def fingerprint_sqlite_database(path: Path) -> SQLiteDatabaseFingerprint:
    """Fingerprint a SQLite main file plus WAL, SHM, and journal sidecars."""
    wal, shm, journal = sqlite_sidecar_paths(path)
    return SQLiteDatabaseFingerprint(
        database=fingerprint_file(path),
        wal=fingerprint_file(wal),
        shm=_fingerprint_sidecar(shm),
        journal=fingerprint_file(journal),
    )


def _fingerprint_sidecar(path: Path) -> FileFingerprint:
    try:
        return fingerprint_file(path)
    except PermissionError:
        stat = path.stat()
        return FileFingerprint(
            exists=True,
            regular_file=path.is_file() and not path.is_symlink(),
            size=stat.st_size,
            modified_ns=stat.st_mtime_ns,
            sha256=None,
        )


def sqlite_sidecar_paths(path: Path) -> tuple[Path, Path, Path]:
    """Return the WAL, SHM, and rollback-journal paths for one SQLite file."""
    return (
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    )


__all__ = [
    "FileFingerprint",
    "SQLiteDatabaseFingerprint",
    "fingerprint_file",
    "fingerprint_sqlite_database",
    "inspect_sqlite_revision",
    "open_read_only_sqlite",
    "open_sqlite_backup_source",
    "sqlite_file_path",
    "sqlite_sidecar_paths",
    "validate_current_sqlite_database",
    "verify_sqlite_integrity",
]
