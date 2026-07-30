"""Verified, atomic backup archives for active file-based SQLite databases."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import stat
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Final, Never
from uuid import UUID, uuid4

from app.application.backup import (
    BackupKind,
    BackupListing,
    BackupRecord,
    BackupService,
    InvalidBackup,
)
from app.core.exceptions import (
    BackupCreationError,
    BackupError,
    BackupNotSupportedError,
    BackupVerificationError,
    ConcurrentDatabaseChangeError,
    DatabaseError,
)
from app.core.settings import Settings
from app.infrastructure.persistence.sqlite_validation import (
    FileFingerprint,
    SQLiteDatabaseFingerprint,
    fingerprint_file,
    fingerprint_sqlite_database,
    open_read_only_sqlite,
    sqlite_file_path,
    sqlite_sidecar_paths,
    validate_current_sqlite_database,
)

BACKUP_EXTENSION: Final = ".mirabackup"
MANIFEST_MEMBER: Final = "manifest.json"
DATABASE_MEMBER: Final = "database.sqlite"
FORMAT_VERSION: Final = 1
MAX_ARCHIVE_MEMBERS: Final = 2
MAX_MANIFEST_BYTES: Final = 16 * 1024
MAX_DATABASE_BYTES: Final = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES: Final = MAX_DATABASE_BYTES + (64 * 1024 * 1024)
STREAM_CHUNK_SIZE: Final = 1024 * 1024
EXPECTED_MANIFEST_FIELDS: Final = frozenset(
    {
        "format_version",
        "application_name",
        "application_version",
        "backup_kind",
        "created_at_utc",
        "alembic_revision",
        "database_filename",
        "database_size_bytes",
        "database_sha256",
    }
)
PERMITTED_MEMBERS: Final = frozenset({MANIFEST_MEMBER, DATABASE_MEMBER})


@dataclass(frozen=True, slots=True)
class _BackupManifest:
    format_version: int
    application_name: str
    application_version: str
    backup_kind: BackupKind
    created_at: datetime
    alembic_revision: str
    database_filename: str
    database_size: int
    database_sha256: str


class SQLiteBackupService(BackupService):
    """Create and verify strict backup archives for one configured SQLite database."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
        script_location: Path | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or _utc_now
        self._uuid_factory = uuid_factory or uuid4
        self._script_location = script_location

    def create_backup(self, kind: BackupKind = BackupKind.MANUAL) -> BackupRecord:
        """Create, independently verify, and atomically install one backup archive."""
        if kind is not BackupKind.MANUAL:
            raise BackupCreationError("Only manual backups are available in this release.")
        source = self._source_path()
        _require_regular_source(source)
        backup_directory = self._settings.backup_directory
        _create_backup_directory(backup_directory)

        created_at = _require_utc(self._clock(), creation=True)
        final_path = self._unique_backup_path(backup_directory, kind, created_at)
        staging_database: Path | None = None
        temporary_archive: Path | None = None
        installed = False

        try:
            staging_database = _reserve_temporary_file(
                backup_directory,
                prefix=".backup-",
                suffix=".sqlite",
                verification=False,
            )
            temporary_archive = _reserve_temporary_file(
                backup_directory,
                prefix=".backup-",
                suffix=f".tmp{BACKUP_EXTENSION}",
                verification=False,
            )
            before = _source_fingerprint(source)
            _copy_database(source, staging_database)
            after = _source_fingerprint(source)
            if not _source_is_unchanged(before, after):
                raise ConcurrentDatabaseChangeError(
                    "The database changed while the backup was being created; retry the operation."
                )

            try:
                revision = validate_current_sqlite_database(
                    staging_database,
                    script_location=self._script_location,
                )
            except DatabaseError as error:
                raise BackupCreationError(
                    "The database must be current and valid before it can be backed up."
                ) from error

            database_fingerprint = fingerprint_file(staging_database)
            database_size, database_sha256 = _regular_fingerprint_values(
                database_fingerprint,
                error_type=BackupCreationError,
            )
            if database_size > MAX_DATABASE_BYTES:
                raise BackupCreationError("The database exceeds the supported backup size.")

            manifest = _BackupManifest(
                format_version=FORMAT_VERSION,
                application_name=self._settings.app_name,
                application_version=self._settings.app_version,
                backup_kind=kind,
                created_at=created_at,
                alembic_revision=revision,
                database_filename=DATABASE_MEMBER,
                database_size=database_size,
                database_sha256=database_sha256,
            )
            _write_archive(temporary_archive, staging_database, manifest)
            verified = self.verify_backup(temporary_archive)
            _remove_database_files(staging_database)
            _install_without_overwrite(temporary_archive, final_path)
            installed = True
            return replace(
                verified,
                path=final_path,
                filename=final_path.name,
                backup_size=final_path.stat().st_size,
            )
        except BackupError:
            if installed:
                final_path.unlink(missing_ok=True)
            raise
        except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
            if installed:
                final_path.unlink(missing_ok=True)
            raise BackupCreationError("The backup could not be created safely.") from error
        finally:
            if staging_database is not None:
                _best_effort_remove_database_files(staging_database)
            if temporary_archive is not None:
                _best_effort_unlink(temporary_archive)

    def verify_backup(self, path: Path) -> BackupRecord:
        """Fully verify one archive without restoring or migrating its database."""
        _require_backup_archive_path(path)
        validation_database = _reserve_temporary_file(
            path.parent,
            prefix=".verify-",
            suffix=".sqlite",
            verification=True,
        )
        try:
            manifest = self._inspect_and_extract_archive(path, validation_database)
            try:
                revision = validate_current_sqlite_database(
                    validation_database,
                    script_location=self._script_location,
                )
            except DatabaseError as error:
                raise BackupVerificationError(
                    "Backup database integrity, revision, or schema validation failed."
                ) from error
            if not hmac.compare_digest(revision, manifest.alembic_revision):
                raise BackupVerificationError("Backup revision does not match its manifest.")
            record = BackupRecord(
                path=path,
                filename=path.name,
                created_at=manifest.created_at,
                app_version=manifest.application_version,
                alembic_revision=manifest.alembic_revision,
                database_size=manifest.database_size,
                database_sha256=manifest.database_sha256,
                backup_size=path.stat().st_size,
                backup_kind=manifest.backup_kind,
            )
            _remove_database_files(validation_database, verification=True)
            return record
        except BackupVerificationError:
            raise
        except (OSError, sqlite3.Error, zipfile.BadZipFile, UnicodeError) as error:
            raise BackupVerificationError("Backup archive verification failed.") from error
        finally:
            _best_effort_remove_database_files(validation_database)

    def list_backups(self) -> BackupListing:
        """Verify configured-directory archives without recursion or filesystem writes."""
        directory = self._settings.backup_directory
        if not directory.exists():
            return BackupListing((), ())
        if directory.is_symlink() or not directory.is_dir():
            raise BackupVerificationError("The configured backup directory is invalid.")

        valid: list[BackupRecord] = []
        invalid: list[InvalidBackup] = []
        try:
            candidates = sorted(
                (
                    candidate
                    for candidate in directory.iterdir()
                    if candidate.suffix == BACKUP_EXTENSION
                ),
                key=lambda candidate: candidate.name,
            )
        except OSError as error:
            raise BackupVerificationError("The backup directory could not be inspected.") from error

        for candidate in candidates:
            if candidate.is_symlink() or not candidate.is_file():
                invalid.append(InvalidBackup(candidate.name, "Backup entry is not a regular file."))
                continue
            try:
                valid.append(self.verify_backup(candidate))
            except BackupVerificationError as error:
                invalid.append(InvalidBackup(candidate.name, str(error)))

        valid.sort(key=lambda record: record.filename)
        valid.sort(key=lambda record: record.created_at, reverse=True)
        return BackupListing(tuple(valid), tuple(invalid))

    def _source_path(self) -> Path:
        try:
            return sqlite_file_path(self._settings.database_url)
        except ValueError as error:
            raise BackupNotSupportedError(
                "Backups require a configured file-based SQLite database."
            ) from error

    def _unique_backup_path(
        self,
        directory: Path,
        kind: BackupKind,
        created_at: datetime,
    ) -> Path:
        timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
        for _ in range(16):
            token = self._uuid_factory().hex[:12]
            filename = f"mira-portfolio-{kind.value}-{timestamp}-{token}{BACKUP_EXTENSION}"
            candidate = directory / filename
            if not candidate.exists():
                return candidate
        raise BackupCreationError("A unique backup filename could not be generated.")

    def _inspect_and_extract_archive(
        self,
        archive_path: Path,
        validation_database: Path,
    ) -> _BackupManifest:
        if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise BackupVerificationError("Backup archive exceeds the supported size.")
        try:
            with zipfile.ZipFile(archive_path, mode="r") as archive:
                infos = archive.infolist()
                _validate_members(infos)
                manifest_info = archive.getinfo(MANIFEST_MEMBER)
                database_info = archive.getinfo(DATABASE_MEMBER)
                manifest = _read_manifest(archive, manifest_info, self._settings)
                if database_info.file_size != manifest.database_size:
                    raise BackupVerificationError(
                        "Backup database size does not match its manifest."
                    )
                if database_info.file_size > MAX_DATABASE_BYTES:
                    raise BackupVerificationError("Backup database exceeds the supported size.")
                extracted_size, extracted_sha256 = _extract_database(
                    archive,
                    database_info,
                    validation_database,
                )
        except KeyError as error:
            raise BackupVerificationError("Backup archive members are incomplete.") from error

        if extracted_size != manifest.database_size:
            raise BackupVerificationError("Backup database size does not match its manifest.")
        if not hmac.compare_digest(extracted_sha256, manifest.database_sha256):
            raise BackupVerificationError("Backup database checksum verification failed.")
        return manifest


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_utc(value: datetime, *, creation: bool) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        message = (
            "Backup creation requires a timezone-aware UTC clock."
            if creation
            else "Backup manifest timestamp must be timezone-aware UTC."
        )
        error_type = BackupCreationError if creation else BackupVerificationError
        raise error_type(message)
    return value.astimezone(UTC)


def _require_regular_source(source: Path) -> None:
    if not source.exists():
        raise BackupCreationError("The configured database file does not exist.")
    if source.is_symlink() or not source.is_file():
        raise BackupCreationError("The configured database is not a regular file.")


def _create_backup_directory(directory: Path) -> None:
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise BackupCreationError("The configured backup directory is invalid.")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise BackupCreationError("The backup directory could not be created.") from error


def _reserve_temporary_file(
    directory: Path,
    *,
    prefix: str,
    suffix: str,
    verification: bool,
) -> Path:
    try:
        with NamedTemporaryFile(
            mode="wb",
            prefix=prefix,
            suffix=suffix,
            dir=directory,
            delete=False,
        ) as temporary:
            return Path(temporary.name)
    except OSError as error:
        if verification:
            raise BackupVerificationError(
                "A temporary backup validation file could not be created."
            ) from error
        raise BackupCreationError("Temporary backup files could not be created.") from error


def _source_fingerprint(source: Path) -> SQLiteDatabaseFingerprint:
    try:
        fingerprint = fingerprint_sqlite_database(source)
    except OSError as error:
        raise ConcurrentDatabaseChangeError(
            "The database changed while its files were being fingerprinted."
        ) from error
    for file_fingerprint in (
        fingerprint.database,
        fingerprint.wal,
        fingerprint.shm,
        fingerprint.journal,
    ):
        if file_fingerprint.exists and not file_fingerprint.regular_file:
            raise BackupCreationError("A database file or sidecar is not a regular file.")
    return fingerprint


def _source_is_unchanged(
    before: SQLiteDatabaseFingerprint,
    after: SQLiteDatabaseFingerprint,
) -> bool:
    """Ignore only SHM reader-lock bytes that SQLite itself may update."""
    return (
        before.database == after.database
        and before.wal == after.wal
        and before.journal == after.journal
        and before.shm.exists == after.shm.exists
        and before.shm.regular_file == after.shm.regular_file
        and before.shm.size == after.shm.size
        and before.shm.modified_ns == after.shm.modified_ns
    )


def _copy_database(source: Path, destination: Path) -> None:
    with closing(open_read_only_sqlite(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)
            destination_connection.commit()


def _regular_fingerprint_values(
    fingerprint: FileFingerprint,
    *,
    error_type: type[BackupCreationError],
) -> tuple[int, str]:
    if (
        not fingerprint.exists
        or not fingerprint.regular_file
        or fingerprint.size is None
        or fingerprint.sha256 is None
    ):
        raise error_type("The staged backup database is not a regular file.")
    return fingerprint.size, fingerprint.sha256


def _manifest_bytes(manifest: _BackupManifest) -> bytes:
    payload: dict[str, object] = {
        "format_version": manifest.format_version,
        "application_name": manifest.application_name,
        "application_version": manifest.application_version,
        "backup_kind": manifest.backup_kind.value,
        "created_at_utc": _format_utc(manifest.created_at),
        "alembic_revision": manifest.alembic_revision,
        "database_filename": manifest.database_filename,
        "database_size_bytes": manifest.database_size,
        "database_sha256": manifest.database_sha256,
    }
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _write_archive(
    archive_path: Path,
    database_path: Path,
    manifest: _BackupManifest,
) -> None:
    manifest_data = _manifest_bytes(manifest)
    if len(manifest_data) > MAX_MANIFEST_BYTES:
        raise BackupCreationError("The backup manifest exceeds the supported size.")
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        archive.writestr(_regular_zip_info(MANIFEST_MEMBER), manifest_data)
        database_info = _regular_zip_info(DATABASE_MEMBER)
        with database_path.open("rb") as source:
            with archive.open(database_info, mode="w", force_zip64=True) as destination:
                shutil.copyfileobj(source, destination, length=STREAM_CHUNK_SIZE)


def _regular_zip_info(filename: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=filename, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _require_backup_archive_path(path: Path) -> None:
    if path.suffix != BACKUP_EXTENSION:
        raise BackupVerificationError("Backup archive must use the .mirabackup extension.")
    if not path.exists():
        raise BackupVerificationError("Backup archive does not exist.")
    if path.is_symlink() or not path.is_file():
        raise BackupVerificationError("Backup archive is not a regular file.")


def _validate_members(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise BackupVerificationError("Backup archive contains too many members.")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise BackupVerificationError("Backup archive contains duplicate members.")
    for info in infos:
        _validate_member_path(info.filename)
        if info.is_dir():
            raise BackupVerificationError("Backup archive contains a directory member.")
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG}:
            raise BackupVerificationError("Backup archive contains a non-regular member.")
        if info.flag_bits & 0x1:
            raise BackupVerificationError("Encrypted backup members are not supported.")
        if info.file_size < 0 or info.compress_size < 0:
            raise BackupVerificationError("Backup archive contains an invalid member size.")
    if len(infos) != MAX_ARCHIVE_MEMBERS or set(names) != PERMITTED_MEMBERS:
        raise BackupVerificationError("Backup archive members are incomplete or unexpected.")
    manifest_info = next(info for info in infos if info.filename == MANIFEST_MEMBER)
    database_info = next(info for info in infos if info.filename == DATABASE_MEMBER)
    if manifest_info.file_size > MAX_MANIFEST_BYTES:
        raise BackupVerificationError("Backup manifest exceeds the supported size.")
    if database_info.file_size > MAX_DATABASE_BYTES:
        raise BackupVerificationError("Backup database exceeds the supported size.")


def _validate_member_path(filename: str) -> None:
    path = PurePosixPath(filename)
    if (
        path.is_absolute()
        or len(path.parts) != 1
        or ".." in path.parts
        or "\\" in filename
        or filename.startswith("/")
    ):
        raise BackupVerificationError("Backup archive contains an unsafe member path.")


def _read_manifest(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    settings: Settings,
) -> _BackupManifest:
    with archive.open(info, mode="r") as stream:
        data = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise BackupVerificationError("Backup manifest exceeds the supported size.")
    try:
        parsed: object = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        raise BackupVerificationError("Backup manifest is not valid strict JSON.") from error
    if not isinstance(parsed, dict):
        raise BackupVerificationError("Backup manifest must be a JSON object.")
    if set(parsed) != EXPECTED_MANIFEST_FIELDS:
        raise BackupVerificationError("Backup manifest fields are incomplete or unexpected.")

    format_version = _manifest_int(parsed, "format_version")
    if format_version != FORMAT_VERSION:
        raise BackupVerificationError("Backup format version is not supported.")
    application_name = _manifest_string(parsed, "application_name")
    application_version = _manifest_string(parsed, "application_version")
    if application_name != settings.app_name:
        raise BackupVerificationError("Backup belongs to a different application.")
    if not application_version:
        raise BackupVerificationError("Backup application version is invalid.")
    backup_kind_value = _manifest_string(parsed, "backup_kind")
    try:
        backup_kind = BackupKind(backup_kind_value)
    except ValueError as error:
        raise BackupVerificationError("Backup kind is not supported.") from error
    created_at = _parse_utc(_manifest_string(parsed, "created_at_utc"))
    alembic_revision = _manifest_string(parsed, "alembic_revision")
    if not alembic_revision:
        raise BackupVerificationError("Backup revision is invalid.")
    database_filename = _manifest_string(parsed, "database_filename")
    if database_filename != DATABASE_MEMBER:
        raise BackupVerificationError("Backup database filename is invalid.")
    database_size = _manifest_int(parsed, "database_size_bytes")
    if database_size <= 0 or database_size > MAX_DATABASE_BYTES:
        raise BackupVerificationError("Backup database size is invalid or unsupported.")
    database_sha256 = _manifest_string(parsed, "database_sha256")
    if not _valid_sha256(database_sha256):
        raise BackupVerificationError("Backup database checksum format is invalid.")
    return _BackupManifest(
        format_version=format_version,
        application_name=application_name,
        application_version=application_version,
        backup_kind=backup_kind,
        created_at=created_at,
        alembic_revision=alembic_revision,
        database_filename=database_filename,
        database_size=database_size,
        database_sha256=database_sha256,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Never:
    raise ValueError(f"Non-standard JSON constant {value!r}.")


def _manifest_string(manifest: dict[str, object], field: str) -> str:
    value = manifest[field]
    if not isinstance(value, str):
        raise BackupVerificationError(f"Backup manifest field {field!r} must be text.")
    return value


def _manifest_int(manifest: dict[str, object], field: str) -> int:
    value = manifest[field]
    if type(value) is not int:
        raise BackupVerificationError(f"Backup manifest field {field!r} must be an integer.")
    return value


def _parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise BackupVerificationError("Backup manifest timestamp must use UTC.")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise BackupVerificationError("Backup manifest timestamp is invalid.") from error
    parsed = _require_utc(parsed, creation=False)
    if _format_utc(parsed) != value:
        raise BackupVerificationError("Backup manifest timestamp is not canonical UTC.")
    return parsed


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _extract_database(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with archive.open(info, mode="r") as source:
        with destination.open("wb") as output:
            while True:
                block = source.read(STREAM_CHUNK_SIZE)
                if not block:
                    break
                total += len(block)
                if total > MAX_DATABASE_BYTES:
                    raise BackupVerificationError("Backup database exceeds the supported size.")
                digest.update(block)
                output.write(block)
    return total, digest.hexdigest()


def _install_without_overwrite(temporary: Path, final: Path) -> None:
    try:
        os.link(temporary, final)
    except FileExistsError as error:
        raise BackupCreationError("A backup with the generated filename already exists.") from error
    except OSError as error:
        raise BackupCreationError(
            "The completed backup could not be installed atomically."
        ) from error
    try:
        temporary.unlink()
    except OSError as error:
        final.unlink(missing_ok=True)
        raise BackupCreationError("Temporary backup files could not be removed.") from error


def _remove_database_files(database: Path, *, verification: bool = False) -> None:
    for path in (database, *sqlite_sidecar_paths(database)):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            if verification:
                raise BackupVerificationError(
                    "Temporary backup validation files could not be removed."
                ) from error
            raise BackupCreationError("Temporary database files could not be removed.") from error


def _best_effort_remove_database_files(database: Path) -> None:
    for path in (database, *sqlite_sidecar_paths(database)):
        _best_effort_unlink(path)


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "BACKUP_EXTENSION",
    "DATABASE_MEMBER",
    "FORMAT_VERSION",
    "MANIFEST_MEMBER",
    "MAX_ARCHIVE_MEMBERS",
    "MAX_DATABASE_BYTES",
    "MAX_MANIFEST_BYTES",
    "SQLiteBackupService",
]
