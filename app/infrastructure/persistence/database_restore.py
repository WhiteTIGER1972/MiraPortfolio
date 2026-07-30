"""Restart-safe staging, application, rollback, and recovery for SQLite restores."""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final, Never
from uuid import UUID, uuid4

from app.application.backup import BackupKind, BackupRecord, BackupService
from app.application.restore import (
    RestoreApplicationResult,
    RestoreBackupIdentity,
    RestoreOutcome,
    RestoreService,
    RestoreStageResult,
)
from app.core.exceptions import (
    BackupError,
    DatabaseError,
    PendingRestoreCorruptError,
    RestoreAlreadyPendingError,
    RestoreApplicationError,
    RestoreError,
    RestoreNotSupportedError,
    RestoreRecoveryError,
    RestoreStagingError,
    RestoreVerificationError,
)
from app.core.settings import Settings
from app.infrastructure.persistence.database_backup import (
    BACKUP_EXTENSION,
    MAX_DATABASE_BYTES,
    SQLiteBackupService,
    extract_verified_backup_payload,
)
from app.infrastructure.persistence.database_preparation import (
    prepare_isolated_sqlite_database,
)
from app.infrastructure.persistence.sqlite_validation import (
    fingerprint_file,
    inspect_sqlite_revision,
    sqlite_file_path,
    sqlite_sidecar_paths,
    validate_current_sqlite_database,
)

RESTORE_DIRECTORY_NAME: Final = "restore"
PENDING_FILENAME: Final = "pending.json"
OPERATION_FILENAME: Final = "operation.json"
PENDING_FORMAT_VERSION: Final = 1
OPERATION_FORMAT_VERSION: Final = 1
UNVERSIONED_REVISION: Final = "unversioned"
MAX_PENDING_BYTES: Final = 32 * 1024
MAX_OPERATION_BYTES: Final = 64 * 1024
MAX_TEXT_LENGTH: Final = 255
FILE_KEYS: Final = ("database", "wal", "shm", "journal")
PENDING_FIELDS: Final = frozenset(
    {
        "format_version",
        "request_id",
        "created_at_utc",
        "staged_database_filename",
        "source_backup_filename",
        "source_backup_sha256",
        "source_application_version",
        "source_alembic_revision",
        "source_database_size_bytes",
        "source_database_sha256",
        "backup_kind",
        "expected_database_size_bytes",
        "expected_database_sha256",
        "expected_alembic_revision",
    }
)
OPERATION_FIELDS: Final = frozenset(
    {
        "format_version",
        "request_id",
        "state",
        "created_at_utc",
        "updated_at_utc",
        "target_database_filename",
        "staged_database_filename",
        "rollback_database_filename",
        "pre_restore_backup_filename",
        "source_backup_filename",
        "source_application_version",
        "source_alembic_revision",
        "source_database_size_bytes",
        "source_database_sha256",
        "backup_kind",
        "expected_database_size_bytes",
        "expected_database_sha256",
        "expected_alembic_revision",
        "original_files",
    }
)
STORED_FILE_FIELDS: Final = frozenset({"filename", "size_bytes", "modified_ns", "sha256"})


class _OperationState(StrEnum):
    PREPARED = "prepared"
    ORIGINAL_PRESERVED = "original_preserved"
    RESTORED_INSTALLED = "restored_installed"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class _RestorePaths:
    target: Path
    workspace: Path
    pending: Path
    operation: Path


@dataclass(frozen=True, slots=True)
class _PendingRestore:
    request_id: UUID
    created_at: datetime
    staged_database_filename: str
    source_backup_filename: str
    source_backup_sha256: str
    source_application_version: str
    source_alembic_revision: str
    source_database_size: int
    source_database_sha256: str
    backup_kind: BackupKind
    expected_database_size: int
    expected_database_sha256: str
    expected_alembic_revision: str


@dataclass(frozen=True, slots=True)
class _StoredFile:
    filename: str
    size: int
    modified_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _Operation:
    request_id: UUID
    state: _OperationState
    created_at: datetime
    updated_at: datetime
    target_database_filename: str
    staged_database_filename: str
    rollback_database_filename: str
    pre_restore_backup_filename: str | None
    source_backup_filename: str
    source_application_version: str
    source_alembic_revision: str
    source_database_size: int
    source_database_sha256: str
    backup_kind: BackupKind
    expected_database_size: int
    expected_database_sha256: str
    expected_alembic_revision: str
    original_files: tuple[
        _StoredFile | None,
        _StoredFile | None,
        _StoredFile | None,
        _StoredFile | None,
    ]


class SQLiteRestoreService(RestoreService):
    """Stage verified restore requests without touching the active database."""

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

    def stage_restore(self, backup_path: Path) -> RestoreStageResult:
        """Stage one independently owned, current database for startup installation."""
        paths = _supported_paths(self._settings)
        _ensure_workspace(paths.workspace)
        _require_no_pending_or_operation(paths)
        request_id = self._uuid_factory()
        staged_filename = f"staged-{request_id}.sqlite"
        staged = paths.workspace / staged_filename
        temporary: Path | None = None

        try:
            archive_before = _required_regular_fingerprint(
                backup_path,
                RestoreStagingError("The selected backup is not a regular file."),
            )
            temporary = _reserve_file(
                paths.workspace,
                prefix=".restore-stage-",
                suffix=".sqlite",
                verification=False,
            )
            backup = extract_verified_backup_payload(
                self._settings,
                backup_path,
                temporary,
            )
            archive_after = _required_regular_fingerprint(
                backup_path,
                RestoreStagingError("The selected backup became unavailable."),
            )
            if archive_before != archive_after:
                raise RestoreStagingError("The selected backup changed while it was being staged.")

            _validate_source_revision(
                temporary,
                backup,
                script_location=self._script_location,
            )
            prepare_isolated_sqlite_database(
                temporary,
                script_location=self._script_location,
            )
            head = validate_current_sqlite_database(
                temporary,
                script_location=self._script_location,
            )
            final_fingerprint = _required_regular_fingerprint(
                temporary,
                RestoreStagingError("The staged database is unavailable."),
            )
            _remove_sidecars(temporary, RestoreStagingError)
            os.replace(temporary, staged)
            temporary = None
            _set_private_permissions(staged)

            pending = _PendingRestore(
                request_id=request_id,
                created_at=_require_utc(self._clock(), RestoreStagingError),
                staged_database_filename=staged_filename,
                source_backup_filename=backup.filename,
                source_backup_sha256=archive_before.sha256,
                source_application_version=backup.app_version,
                source_alembic_revision=backup.alembic_revision,
                source_database_size=backup.database_size,
                source_database_sha256=backup.database_sha256,
                backup_kind=backup.backup_kind,
                expected_database_size=final_fingerprint.size,
                expected_database_sha256=final_fingerprint.sha256,
                expected_alembic_revision=head,
            )
            _write_json_exclusive(paths.pending, _pending_payload(pending))
            return _stage_result(pending)
        except RestoreError:
            _best_effort_remove_database(temporary)
            _best_effort_remove_database(staged)
            _remove_empty_workspace(paths.workspace)
            raise
        except (BackupError, DatabaseError, OSError) as error:
            _best_effort_remove_database(temporary)
            _best_effort_remove_database(staged)
            _remove_empty_workspace(paths.workspace)
            raise RestoreStagingError(
                "The backup could not be staged safely for restart."
            ) from error

    def get_pending_restore(self) -> RestoreStageResult | None:
        """Return pending state only after its staged database is revalidated."""
        paths = _supported_paths(self._settings)
        if not paths.workspace.exists():
            return None
        _require_workspace(paths.workspace)
        if paths.operation.exists():
            raise RestoreVerificationError("A restore operation requires startup recovery.")
        if not paths.pending.exists():
            _require_no_orphan_artifacts(paths)
            return None
        pending = _load_pending(paths)
        _require_clean_pending_workspace(paths, pending)
        _validate_staged_database(
            paths,
            pending,
            script_location=self._script_location,
        )
        return _stage_result(pending)

    def cancel_pending_restore(self) -> bool:
        """Cancel only a fully validated request confined to its restore workspace."""
        paths = _supported_paths(self._settings)
        if not paths.workspace.exists():
            return False
        _require_workspace(paths.workspace)
        if paths.operation.exists():
            raise RestoreVerificationError(
                "A restore operation requires startup recovery before cancellation."
            )
        if not paths.pending.exists():
            _require_no_orphan_artifacts(paths)
            return False

        pending = _load_pending(paths)
        _require_clean_pending_workspace(paths, pending)
        staged = _validate_staged_database(
            paths,
            pending,
            script_location=self._script_location,
        )
        held = paths.workspace / f".cancel-{pending.request_id}.json"
        try:
            os.replace(paths.pending, held)
            _remove_database(staged, RestoreVerificationError)
            held.unlink()
            _remove_empty_workspace(paths.workspace)
            return True
        except OSError as error:
            if held.exists() and not paths.pending.exists():
                try:
                    os.replace(held, paths.pending)
                except OSError as recovery_error:
                    raise RestoreRecoveryError(
                        "Pending restore cancellation could not be rolled back."
                    ) from recovery_error
            raise RestoreVerificationError(
                "Pending restore cancellation could not complete safely."
            ) from error


class StartupRestoreCoordinator:
    """Apply or recover a pending restore before any runtime engine exists."""

    def __init__(
        self,
        settings: Settings,
        *,
        backup_service: BackupService | None = None,
        clock: Callable[[], datetime] | None = None,
        script_location: Path | None = None,
    ) -> None:
        self._settings = settings
        self._backup_service = backup_service or SQLiteBackupService(settings)
        self._clock = clock or _utc_now
        self._script_location = script_location

    def apply_pending_restore(self) -> RestoreApplicationResult:
        """Recover an interrupted operation or atomically apply one pending restore."""
        now = _require_utc(self._clock(), RestoreApplicationError)
        try:
            paths = _optional_startup_paths(self._settings)
        except RestoreNotSupportedError:
            return _no_pending_result(now)
        if paths is None or not paths.workspace.exists():
            return _no_pending_result(now)
        _require_workspace(paths.workspace)

        if paths.operation.exists():
            return self._recover(paths, now)
        if not paths.pending.exists():
            _require_no_orphan_artifacts(paths)
            return _no_pending_result(now)

        pending = _load_pending(paths)
        _require_clean_pending_workspace(paths, pending)
        staged = _validate_staged_database(
            paths,
            pending,
            script_location=self._script_location,
        )
        _require_target_shape(paths.target)

        pre_restore: BackupRecord | None = None
        if paths.target.exists():
            try:
                pre_restore = self._backup_service.create_backup(BackupKind.PRE_RESTORE)
                self._backup_service.verify_backup(pre_restore.path)
            except BackupError as error:
                raise RestoreApplicationError(
                    "The pre-restore safety backup could not be created and verified."
                ) from error

        original_files = _capture_original_files(paths.target)
        rollback_filename = f"rollback-{pending.request_id}.sqlite"
        operation = _operation_from_pending(
            pending,
            target_filename=paths.target.name,
            rollback_filename=rollback_filename,
            pre_restore_backup_filename=(pre_restore.filename if pre_restore is not None else None),
            original_files=original_files,
            now=now,
        )
        _write_json_exclusive(paths.operation, _operation_payload(operation))

        try:
            if original_files[0] is not None:
                _preserve_original(paths, operation)
                operation = _transition_operation(
                    paths,
                    operation,
                    _OperationState.ORIGINAL_PRESERVED,
                    self._clock,
                )

            os.replace(staged, paths.target)
            operation = _transition_operation(
                paths,
                operation,
                _OperationState.RESTORED_INSTALLED,
                self._clock,
            )
            _validate_installed_database(
                paths.target,
                operation,
                script_location=self._script_location,
            )
            _remove_sidecars(paths.target, RestoreApplicationError)
            operation = _transition_operation(
                paths,
                operation,
                _OperationState.VERIFIED,
                self._clock,
            )
            _finalize_success(paths, operation)
            return RestoreApplicationResult(
                request_id=pending.request_id,
                outcome=RestoreOutcome.APPLIED,
                restored_backup=_final_identity(pending),
                pre_restore_backup=pre_restore,
                applied_at=_require_utc(self._clock(), RestoreApplicationError),
            )
        except Exception as error:
            if operation.state is _OperationState.VERIFIED:
                raise RestoreRecoveryError(
                    "The restored database is valid, but transaction cleanup is incomplete."
                ) from error
            try:
                _rollback_operation(paths, operation)
            except Exception as recovery_error:
                raise RestoreRecoveryError(
                    "Database restore failed and automatic rollback could not complete."
                ) from recovery_error
            raise RestoreApplicationError(
                "Database restore failed; the original database was restored."
            ) from error

    def _recover(
        self,
        paths: _RestorePaths,
        now: datetime,
    ) -> RestoreApplicationResult:
        operation = _load_operation(paths)
        _load_matching_pending_if_present(paths, operation)

        if operation.state is _OperationState.PREPARED:
            rollback = paths.workspace / operation.rollback_database_filename
            if rollback.exists():
                _rollback_operation(paths, operation)
            elif _matches_original_files(paths.target, operation.original_files):
                paths.operation.unlink()
            else:
                raise RestoreRecoveryError(
                    "Interrupted restore state is ambiguous; no files were changed."
                )
            return _recovery_result(
                operation,
                now,
                RestoreOutcome.RECOVERED_INTERRUPTED_OPERATION,
            )

        if operation.state is _OperationState.ORIGINAL_PRESERVED:
            try:
                _rollback_operation(paths, operation)
            except Exception as error:
                raise RestoreRecoveryError(
                    "The preserved original database could not be recovered."
                ) from error
            return _recovery_result(operation, now, RestoreOutcome.ROLLED_BACK)

        if operation.state in {
            _OperationState.RESTORED_INSTALLED,
            _OperationState.VERIFIED,
        }:
            try:
                _validate_installed_database(
                    paths.target,
                    operation,
                    script_location=self._script_location,
                )
                pre_restore = self._recover_pre_restore_backup(operation)
            except (RestoreError, DatabaseError, OSError):
                try:
                    _rollback_operation(paths, operation)
                except Exception as recovery_error:
                    raise RestoreRecoveryError(
                        "Interrupted restore validation and rollback both failed."
                    ) from recovery_error
                return _recovery_result(
                    operation,
                    now,
                    RestoreOutcome.ROLLED_BACK,
                )
            try:
                _remove_sidecars(paths.target, RestoreRecoveryError)
                _finalize_success(paths, operation)
            except (RestoreError, OSError) as error:
                raise RestoreRecoveryError(
                    "The recovered database is valid, but transaction cleanup is incomplete."
                ) from error
            return RestoreApplicationResult(
                request_id=operation.request_id,
                outcome=RestoreOutcome.RECOVERED_INTERRUPTED_OPERATION,
                restored_backup=_operation_identity(operation),
                pre_restore_backup=pre_restore,
                applied_at=now,
            )

        raise RestoreRecoveryError("The restore operation state is not supported.")

    def _recover_pre_restore_backup(
        self,
        operation: _Operation,
    ) -> BackupRecord | None:
        filename = operation.pre_restore_backup_filename
        if filename is None:
            return None
        _require_filename(filename, "pre-restore backup")
        try:
            return self._backup_service.verify_backup(self._settings.backup_directory / filename)
        except BackupError as error:
            raise RestoreRecoveryError(
                "The pre-restore safety backup is missing or invalid."
            ) from error


def apply_pending_restore(settings: Settings) -> RestoreApplicationResult:
    """Apply pending startup restore state without constructing a Container."""
    return StartupRestoreCoordinator(settings).apply_pending_restore()


def _supported_paths(settings: Settings) -> _RestorePaths:
    try:
        target = sqlite_file_path(settings.database_url)
    except ValueError as error:
        raise RestoreNotSupportedError(
            "Restore requires a configured file-based SQLite database."
        ) from error
    workspace = target.parent / RESTORE_DIRECTORY_NAME
    return _RestorePaths(
        target=target,
        workspace=workspace,
        pending=workspace / PENDING_FILENAME,
        operation=workspace / OPERATION_FILENAME,
    )


def _optional_startup_paths(settings: Settings) -> _RestorePaths | None:
    try:
        return _supported_paths(settings)
    except RestoreNotSupportedError:
        return None


def _ensure_workspace(workspace: Path) -> None:
    try:
        workspace.mkdir(mode=0o700, parents=False, exist_ok=True)
        _require_workspace(workspace)
        _set_private_permissions(workspace, directory=True)
    except RestoreError:
        raise
    except OSError as error:
        raise RestoreStagingError("The restore staging directory could not be created.") from error


def _require_workspace(workspace: Path) -> None:
    if workspace.is_symlink() or not workspace.is_dir():
        raise PendingRestoreCorruptError("The restore staging location is not a regular directory.")


def _require_no_pending_or_operation(paths: _RestorePaths) -> None:
    if paths.pending.exists():
        raise RestoreAlreadyPendingError(
            "A restore is already pending; cancel it before staging another."
        )
    if paths.operation.exists():
        raise RestoreRecoveryError(
            "An interrupted restore must be recovered before staging another."
        )
    _require_no_orphan_artifacts(paths)


def _require_no_orphan_artifacts(paths: _RestorePaths) -> None:
    try:
        suspicious = [
            path.name
            for path in paths.workspace.iterdir()
            if path.name.startswith(("rollback-", "staged-", ".cancel-"))
        ]
    except OSError as error:
        raise PendingRestoreCorruptError(
            "The restore staging location could not be inspected."
        ) from error
    if suspicious:
        raise PendingRestoreCorruptError(
            "Unreferenced restore transaction files require manual recovery."
        )


def _require_clean_pending_workspace(
    paths: _RestorePaths,
    pending: _PendingRestore,
) -> None:
    permitted = {PENDING_FILENAME, pending.staged_database_filename}
    try:
        suspicious = [
            path.name
            for path in paths.workspace.iterdir()
            if path.name not in permitted
            and path.name.startswith(
                (
                    "rollback-",
                    "staged-",
                    ".cancel-",
                    ".pending.json.",
                    ".operation.json.",
                )
            )
        ]
    except OSError as error:
        raise PendingRestoreCorruptError(
            "The restore staging location could not be inspected."
        ) from error
    if suspicious:
        raise PendingRestoreCorruptError(
            "Unreferenced restore transaction files require manual recovery."
        )


def _require_target_shape(target: Path) -> None:
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise RestoreApplicationError("The configured database target is not a regular file.")
    if target.exists() and not os.access(target, os.W_OK):
        raise RestoreApplicationError(
            "The configured database target is not writable for offline restore."
        )
    if not target.exists() and any(path.exists() for path in sqlite_sidecar_paths(target)):
        raise RestoreApplicationError("Orphaned database sidecars prevent a safe restore.")
    for sidecar in sqlite_sidecar_paths(target):
        if sidecar.is_symlink() or (sidecar.exists() and not sidecar.is_file()):
            raise RestoreApplicationError("A database sidecar is not a regular file.")
        if sidecar.exists() and not os.access(sidecar, os.W_OK):
            raise RestoreApplicationError("A database sidecar is not writable for offline restore.")


def _reserve_file(
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
            path = Path(temporary.name)
        _set_private_permissions(path)
        return path
    except OSError as error:
        if verification:
            raise RestoreVerificationError(
                "A temporary restore validation file could not be created."
            ) from error
        raise RestoreStagingError(
            "A temporary restore staging file could not be created."
        ) from error


def _validate_source_revision(
    database: Path,
    backup: BackupRecord,
    *,
    script_location: Path | None,
) -> None:
    try:
        revision, _ = inspect_sqlite_revision(
            database,
            script_location=script_location,
        )
    except DatabaseError as error:
        raise RestoreStagingError("The backup database revision is invalid or unknown.") from error
    expected = revision if revision is not None else UNVERSIONED_REVISION
    if not hmac.compare_digest(expected, backup.alembic_revision):
        raise RestoreStagingError("The backup manifest does not match its database revision.")


def _validate_staged_database(
    paths: _RestorePaths,
    pending: _PendingRestore,
    *,
    script_location: Path | None,
) -> Path:
    staged = _confined_file(paths.workspace, pending.staged_database_filename)
    fingerprint = _required_regular_fingerprint(
        staged,
        RestoreVerificationError("The staged restore database is unavailable."),
    )
    if fingerprint.size != pending.expected_database_size or not hmac.compare_digest(
        fingerprint.sha256,
        pending.expected_database_sha256,
    ):
        raise RestoreVerificationError(
            "The staged restore database does not match pending metadata."
        )
    try:
        revision = validate_current_sqlite_database(
            staged,
            script_location=script_location,
        )
    except DatabaseError as error:
        raise RestoreVerificationError(
            "The staged restore database is not current and valid."
        ) from error
    if not hmac.compare_digest(revision, pending.expected_alembic_revision):
        raise RestoreVerificationError(
            "The staged restore revision does not match pending metadata."
        )
    _remove_sidecars(staged, RestoreVerificationError)
    return staged


def _validate_installed_database(
    target: Path,
    operation: _Operation,
    *,
    script_location: Path | None,
) -> None:
    fingerprint = _required_regular_fingerprint(
        target,
        RestoreApplicationError("The installed restore database is unavailable."),
    )
    if fingerprint.size != operation.expected_database_size or not hmac.compare_digest(
        fingerprint.sha256,
        operation.expected_database_sha256,
    ):
        raise RestoreApplicationError("The installed database does not match the staged restore.")
    try:
        revision = validate_current_sqlite_database(
            target,
            script_location=script_location,
        )
    except DatabaseError as error:
        raise RestoreApplicationError(
            "The installed database failed integrity, revision, or schema validation."
        ) from error
    if not hmac.compare_digest(revision, operation.expected_alembic_revision):
        raise RestoreApplicationError("The installed database revision is incorrect.")
    after = _required_regular_fingerprint(
        target,
        RestoreApplicationError("The installed restore database is unavailable."),
    )
    if after != fingerprint:
        raise RestoreApplicationError("The installed database changed during validation.")


def _capture_original_files(
    target: Path,
) -> tuple[
    _StoredFile | None,
    _StoredFile | None,
    _StoredFile | None,
    _StoredFile | None,
]:
    files = (target, *sqlite_sidecar_paths(target))
    captured: list[_StoredFile | None] = []
    for path in files:
        fingerprint = fingerprint_file(path)
        if not fingerprint.exists:
            captured.append(None)
            continue
        if (
            not fingerprint.regular_file
            or fingerprint.size is None
            or fingerprint.modified_ns is None
            or fingerprint.sha256 is None
        ):
            raise RestoreApplicationError(
                "The database or a sidecar could not be fingerprinted safely."
            )
        captured.append(
            _StoredFile(
                filename=path.name,
                size=fingerprint.size,
                modified_ns=fingerprint.modified_ns,
                sha256=fingerprint.sha256,
            )
        )
    return (captured[0], captured[1], captured[2], captured[3])


def _preserve_original(paths: _RestorePaths, operation: _Operation) -> None:
    rollback = paths.workspace / operation.rollback_database_filename
    if rollback.exists() or any(path.exists() for path in sqlite_sidecar_paths(rollback)):
        raise RestoreRecoveryError("An unrelated rollback transaction file already exists.")
    os.replace(paths.target, rollback)
    for source, destination in zip(
        sqlite_sidecar_paths(paths.target),
        sqlite_sidecar_paths(rollback),
        strict=True,
    ):
        if source.exists():
            os.replace(source, destination)


def _rollback_operation(paths: _RestorePaths, operation: _Operation) -> None:
    staged = paths.workspace / operation.staged_database_filename
    rollback = paths.workspace / operation.rollback_database_filename
    original_exists = operation.original_files[0] is not None

    if original_exists:
        if not rollback.exists() and _matches_original_files(
            paths.target,
            operation.original_files,
        ):
            paths.operation.unlink(missing_ok=True)
            return
        if paths.target.exists():
            if staged.exists():
                raise RestoreRecoveryError(
                    "Both restored target and staged database exist; rollback is ambiguous."
                )
            os.replace(paths.target, staged)
            for source, destination in zip(
                sqlite_sidecar_paths(paths.target),
                sqlite_sidecar_paths(staged),
                strict=True,
            ):
                if source.exists():
                    os.replace(source, destination)
        if not rollback.exists():
            raise RestoreRecoveryError("The preserved original database is missing.")
        os.replace(rollback, paths.target)
        for destination, source in zip(
            sqlite_sidecar_paths(paths.target),
            sqlite_sidecar_paths(rollback),
            strict=True,
        ):
            if source.exists():
                os.replace(source, destination)
    else:
        if paths.target.exists():
            if staged.exists():
                raise RestoreRecoveryError(
                    "Both restored target and staged database exist; recovery is ambiguous."
                )
            os.replace(paths.target, staged)
        _remove_sidecars(paths.target, RestoreRecoveryError)

    if not _matches_original_files(paths.target, operation.original_files):
        raise RestoreRecoveryError("The original database fingerprint could not be restored.")
    paths.operation.unlink(missing_ok=True)


def _matches_original_files(
    target: Path,
    stored: tuple[
        _StoredFile | None,
        _StoredFile | None,
        _StoredFile | None,
        _StoredFile | None,
    ],
) -> bool:
    for path, expected in zip(
        (target, *sqlite_sidecar_paths(target)),
        stored,
        strict=True,
    ):
        actual = fingerprint_file(path)
        if expected is None:
            if actual.exists:
                return False
            continue
        if (
            not actual.exists
            or not actual.regular_file
            or actual.size != expected.size
            or actual.modified_ns != expected.modified_ns
            or not hmac.compare_digest(actual.sha256 or "", expected.sha256)
        ):
            return False
    return True


def _transition_operation(
    paths: _RestorePaths,
    operation: _Operation,
    state: _OperationState,
    clock: Callable[[], datetime],
) -> _Operation:
    transitioned = replace(
        operation,
        state=state,
        updated_at=_require_utc(clock(), RestoreApplicationError),
    )
    _write_json_replace(paths.operation, _operation_payload(transitioned))
    return transitioned


def _finalize_success(paths: _RestorePaths, operation: _Operation) -> None:
    staged = paths.workspace / operation.staged_database_filename
    _remove_database(staged, RestoreRecoveryError)
    paths.pending.unlink(missing_ok=True)
    rollback = paths.workspace / operation.rollback_database_filename
    _remove_database(rollback, RestoreRecoveryError)
    paths.operation.unlink()
    _remove_empty_workspace(paths.workspace)


def _operation_from_pending(
    pending: _PendingRestore,
    *,
    target_filename: str,
    rollback_filename: str,
    pre_restore_backup_filename: str | None,
    original_files: tuple[
        _StoredFile | None,
        _StoredFile | None,
        _StoredFile | None,
        _StoredFile | None,
    ],
    now: datetime,
) -> _Operation:
    return _Operation(
        request_id=pending.request_id,
        state=_OperationState.PREPARED,
        created_at=now,
        updated_at=now,
        target_database_filename=target_filename,
        staged_database_filename=pending.staged_database_filename,
        rollback_database_filename=rollback_filename,
        pre_restore_backup_filename=pre_restore_backup_filename,
        source_backup_filename=pending.source_backup_filename,
        source_application_version=pending.source_application_version,
        source_alembic_revision=pending.source_alembic_revision,
        source_database_size=pending.source_database_size,
        source_database_sha256=pending.source_database_sha256,
        backup_kind=pending.backup_kind,
        expected_database_size=pending.expected_database_size,
        expected_database_sha256=pending.expected_database_sha256,
        expected_alembic_revision=pending.expected_alembic_revision,
        original_files=original_files,
    )


def _load_matching_pending_if_present(
    paths: _RestorePaths,
    operation: _Operation,
) -> _PendingRestore | None:
    if not paths.pending.exists():
        return None
    pending = _load_pending(paths)
    if pending.request_id != operation.request_id:
        raise RestoreRecoveryError(
            "Pending restore and operation journal identifiers do not match."
        )
    if (
        pending.staged_database_filename != operation.staged_database_filename
        or pending.source_backup_filename != operation.source_backup_filename
        or pending.source_application_version != operation.source_application_version
        or pending.source_alembic_revision != operation.source_alembic_revision
        or pending.source_database_size != operation.source_database_size
        or not hmac.compare_digest(
            pending.source_database_sha256,
            operation.source_database_sha256,
        )
        or pending.backup_kind is not operation.backup_kind
        or pending.expected_database_size != operation.expected_database_size
        or not hmac.compare_digest(
            pending.expected_database_sha256,
            operation.expected_database_sha256,
        )
        or pending.expected_alembic_revision != operation.expected_alembic_revision
    ):
        raise RestoreRecoveryError("Pending restore and operation journal contents do not match.")
    return pending


def _stage_result(pending: _PendingRestore) -> RestoreStageResult:
    return RestoreStageResult(
        request_id=pending.request_id,
        backup=_source_identity(pending),
        staged_at=pending.created_at,
        restart_required=True,
        outcome=RestoreOutcome.STAGED,
    )


def _source_identity(pending: _PendingRestore) -> RestoreBackupIdentity:
    return RestoreBackupIdentity(
        filename=pending.source_backup_filename,
        app_version=pending.source_application_version,
        alembic_revision=pending.source_alembic_revision,
        database_size=pending.source_database_size,
        database_sha256=pending.source_database_sha256,
        backup_kind=pending.backup_kind,
    )


def _final_identity(pending: _PendingRestore) -> RestoreBackupIdentity:
    return RestoreBackupIdentity(
        filename=pending.source_backup_filename,
        app_version=pending.source_application_version,
        alembic_revision=pending.expected_alembic_revision,
        database_size=pending.expected_database_size,
        database_sha256=pending.expected_database_sha256,
        backup_kind=pending.backup_kind,
    )


def _operation_identity(operation: _Operation) -> RestoreBackupIdentity:
    return RestoreBackupIdentity(
        filename=operation.source_backup_filename,
        app_version=operation.source_application_version,
        alembic_revision=operation.expected_alembic_revision,
        database_size=operation.expected_database_size,
        database_sha256=operation.expected_database_sha256,
        backup_kind=operation.backup_kind,
    )


def _no_pending_result(now: datetime) -> RestoreApplicationResult:
    return RestoreApplicationResult(
        request_id=None,
        outcome=RestoreOutcome.NO_PENDING_RESTORE,
        restored_backup=None,
        pre_restore_backup=None,
        applied_at=now,
    )


def _recovery_result(
    operation: _Operation,
    now: datetime,
    outcome: RestoreOutcome,
) -> RestoreApplicationResult:
    return RestoreApplicationResult(
        request_id=operation.request_id,
        outcome=outcome,
        restored_backup=None,
        pre_restore_backup=None,
        applied_at=now,
    )


def _pending_payload(pending: _PendingRestore) -> dict[str, object]:
    return {
        "format_version": PENDING_FORMAT_VERSION,
        "request_id": str(pending.request_id),
        "created_at_utc": _format_utc(pending.created_at),
        "staged_database_filename": pending.staged_database_filename,
        "source_backup_filename": pending.source_backup_filename,
        "source_backup_sha256": pending.source_backup_sha256,
        "source_application_version": pending.source_application_version,
        "source_alembic_revision": pending.source_alembic_revision,
        "source_database_size_bytes": pending.source_database_size,
        "source_database_sha256": pending.source_database_sha256,
        "backup_kind": pending.backup_kind.value,
        "expected_database_size_bytes": pending.expected_database_size,
        "expected_database_sha256": pending.expected_database_sha256,
        "expected_alembic_revision": pending.expected_alembic_revision,
    }


def _operation_payload(operation: _Operation) -> dict[str, object]:
    return {
        "format_version": OPERATION_FORMAT_VERSION,
        "request_id": str(operation.request_id),
        "state": operation.state.value,
        "created_at_utc": _format_utc(operation.created_at),
        "updated_at_utc": _format_utc(operation.updated_at),
        "target_database_filename": operation.target_database_filename,
        "staged_database_filename": operation.staged_database_filename,
        "rollback_database_filename": operation.rollback_database_filename,
        "pre_restore_backup_filename": operation.pre_restore_backup_filename,
        "source_backup_filename": operation.source_backup_filename,
        "source_application_version": operation.source_application_version,
        "source_alembic_revision": operation.source_alembic_revision,
        "source_database_size_bytes": operation.source_database_size,
        "source_database_sha256": operation.source_database_sha256,
        "backup_kind": operation.backup_kind.value,
        "expected_database_size_bytes": operation.expected_database_size,
        "expected_database_sha256": operation.expected_database_sha256,
        "expected_alembic_revision": operation.expected_alembic_revision,
        "original_files": {
            key: _stored_file_payload(stored)
            for key, stored in zip(FILE_KEYS, operation.original_files, strict=True)
        },
    }


def _stored_file_payload(stored: _StoredFile | None) -> dict[str, object] | None:
    if stored is None:
        return None
    return {
        "filename": stored.filename,
        "size_bytes": stored.size,
        "modified_ns": stored.modified_ns,
        "sha256": stored.sha256,
    }


def _load_pending(paths: _RestorePaths) -> _PendingRestore:
    parsed = _read_json(paths.pending, MAX_PENDING_BYTES, PendingRestoreCorruptError)
    if set(parsed) != PENDING_FIELDS:
        raise PendingRestoreCorruptError(
            "Pending restore metadata fields are incomplete or unexpected."
        )
    if (
        _required_int(parsed, "format_version", PendingRestoreCorruptError)
        != PENDING_FORMAT_VERSION
    ):
        raise PendingRestoreCorruptError("Pending restore metadata version is not supported.")
    request_id = _required_uuid(parsed, "request_id", PendingRestoreCorruptError)
    staged_filename = _required_filename_field(
        parsed,
        "staged_database_filename",
        PendingRestoreCorruptError,
    )
    if staged_filename != f"staged-{request_id}.sqlite":
        raise PendingRestoreCorruptError("Pending restore staged filename is invalid.")
    return _PendingRestore(
        request_id=request_id,
        created_at=_required_utc(parsed, "created_at_utc", PendingRestoreCorruptError),
        staged_database_filename=staged_filename,
        source_backup_filename=_required_backup_filename_field(
            parsed,
            "source_backup_filename",
            PendingRestoreCorruptError,
        ),
        source_backup_sha256=_required_sha256(
            parsed,
            "source_backup_sha256",
            PendingRestoreCorruptError,
        ),
        source_application_version=_required_text(
            parsed,
            "source_application_version",
            PendingRestoreCorruptError,
        ),
        source_alembic_revision=_required_text(
            parsed,
            "source_alembic_revision",
            PendingRestoreCorruptError,
        ),
        source_database_size=_required_size(
            parsed,
            "source_database_size_bytes",
            PendingRestoreCorruptError,
        ),
        source_database_sha256=_required_sha256(
            parsed,
            "source_database_sha256",
            PendingRestoreCorruptError,
        ),
        backup_kind=_required_backup_kind(
            parsed,
            PendingRestoreCorruptError,
        ),
        expected_database_size=_required_size(
            parsed,
            "expected_database_size_bytes",
            PendingRestoreCorruptError,
        ),
        expected_database_sha256=_required_sha256(
            parsed,
            "expected_database_sha256",
            PendingRestoreCorruptError,
        ),
        expected_alembic_revision=_required_text(
            parsed,
            "expected_alembic_revision",
            PendingRestoreCorruptError,
        ),
    )


def _load_operation(paths: _RestorePaths) -> _Operation:
    parsed = _read_json(paths.operation, MAX_OPERATION_BYTES, RestoreRecoveryError)
    if set(parsed) != OPERATION_FIELDS:
        raise RestoreRecoveryError("Restore operation journal fields are incomplete or unexpected.")
    if _required_int(parsed, "format_version", RestoreRecoveryError) != OPERATION_FORMAT_VERSION:
        raise RestoreRecoveryError("Restore operation journal version is not supported.")
    request_id = _required_uuid(parsed, "request_id", RestoreRecoveryError)
    target_filename = _required_filename_field(
        parsed,
        "target_database_filename",
        RestoreRecoveryError,
    )
    if target_filename != paths.target.name:
        raise RestoreRecoveryError("Restore operation target does not match current settings.")
    staged_filename = _required_filename_field(
        parsed,
        "staged_database_filename",
        RestoreRecoveryError,
    )
    rollback_filename = _required_filename_field(
        parsed,
        "rollback_database_filename",
        RestoreRecoveryError,
    )
    if (
        staged_filename != f"staged-{request_id}.sqlite"
        or rollback_filename != f"rollback-{request_id}.sqlite"
    ):
        raise RestoreRecoveryError("Restore operation filenames are invalid.")
    state_text = _required_text(parsed, "state", RestoreRecoveryError)
    try:
        state = _OperationState(state_text)
    except ValueError as error:
        raise RestoreRecoveryError("Restore operation state is not supported.") from error
    original_files = _parse_original_files(parsed, paths)
    return _Operation(
        request_id=request_id,
        state=state,
        created_at=_required_utc(parsed, "created_at_utc", RestoreRecoveryError),
        updated_at=_required_utc(parsed, "updated_at_utc", RestoreRecoveryError),
        target_database_filename=target_filename,
        staged_database_filename=staged_filename,
        rollback_database_filename=rollback_filename,
        pre_restore_backup_filename=_optional_filename_field(
            parsed,
            "pre_restore_backup_filename",
            RestoreRecoveryError,
        ),
        source_backup_filename=_required_backup_filename_field(
            parsed,
            "source_backup_filename",
            RestoreRecoveryError,
        ),
        source_application_version=_required_text(
            parsed,
            "source_application_version",
            RestoreRecoveryError,
        ),
        source_alembic_revision=_required_text(
            parsed,
            "source_alembic_revision",
            RestoreRecoveryError,
        ),
        source_database_size=_required_size(
            parsed,
            "source_database_size_bytes",
            RestoreRecoveryError,
        ),
        source_database_sha256=_required_sha256(
            parsed,
            "source_database_sha256",
            RestoreRecoveryError,
        ),
        backup_kind=_required_backup_kind(parsed, RestoreRecoveryError),
        expected_database_size=_required_size(
            parsed,
            "expected_database_size_bytes",
            RestoreRecoveryError,
        ),
        expected_database_sha256=_required_sha256(
            parsed,
            "expected_database_sha256",
            RestoreRecoveryError,
        ),
        expected_alembic_revision=_required_text(
            parsed,
            "expected_alembic_revision",
            RestoreRecoveryError,
        ),
        original_files=original_files,
    )


def _parse_original_files(
    parsed: Mapping[str, object],
    paths: _RestorePaths,
) -> tuple[
    _StoredFile | None,
    _StoredFile | None,
    _StoredFile | None,
    _StoredFile | None,
]:
    raw = parsed["original_files"]
    if not isinstance(raw, dict) or set(raw) != set(FILE_KEYS):
        raise RestoreRecoveryError("Restore original-file fingerprints are invalid.")
    expected_names = (
        paths.target.name,
        f"{paths.target.name}-wal",
        f"{paths.target.name}-shm",
        f"{paths.target.name}-journal",
    )
    stored = tuple(
        _parse_stored_file(raw[key], expected_name)
        for key, expected_name in zip(FILE_KEYS, expected_names, strict=True)
    )
    return (stored[0], stored[1], stored[2], stored[3])


def _parse_stored_file(value: object, expected_filename: str) -> _StoredFile | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != STORED_FILE_FIELDS:
        raise RestoreRecoveryError("A restore file fingerprint is invalid.")
    filename = _required_filename_field(value, "filename", RestoreRecoveryError)
    if filename != expected_filename:
        raise RestoreRecoveryError("A restore file fingerprint filename is invalid.")
    size = _required_nonnegative_int(value, "size_bytes", RestoreRecoveryError)
    modified_ns = _required_nonnegative_int(value, "modified_ns", RestoreRecoveryError)
    return _StoredFile(
        filename=filename,
        size=size,
        modified_ns=modified_ns,
        sha256=_required_sha256(value, "sha256", RestoreRecoveryError),
    )


def _read_json(
    path: Path,
    maximum: int,
    error_type: type[RestoreError],
) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise error_type("Restore metadata is not a regular file.")
    try:
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise error_type("Restore metadata exceeds the supported size.")
        parsed: object = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except RestoreError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise error_type("Restore metadata is not valid strict JSON.") from error
    if not isinstance(parsed, dict):
        raise error_type("Restore metadata must be a JSON object.")
    return parsed


def _write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    temporary = _write_json_temporary(path.parent, path.name, payload)
    try:
        os.link(temporary, path)
    except FileExistsError as error:
        raise RestoreAlreadyPendingError("Restore metadata already exists.") from error
    except OSError as error:
        raise RestoreStagingError("Restore metadata could not be installed atomically.") from error
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_replace(path: Path, payload: Mapping[str, object]) -> None:
    temporary = _write_json_temporary(path.parent, path.name, payload)
    try:
        os.replace(temporary, path)
    except OSError as error:
        raise RestoreApplicationError(
            "Restore operation journal could not be updated atomically."
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_temporary(
    directory: Path,
    name: str,
    payload: Mapping[str, object],
) -> Path:
    data = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    try:
        with NamedTemporaryFile(
            mode="wb",
            prefix=f".{name}.",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            path = Path(temporary.name)
        _set_private_permissions(path)
        return path
    except OSError as error:
        raise RestoreStagingError("Restore metadata could not be written safely.") from error


def _required_regular_fingerprint(
    path: Path,
    error: RestoreError,
) -> _StoredFile:
    try:
        fingerprint = fingerprint_file(path)
    except OSError as cause:
        raise error from cause
    if (
        not fingerprint.exists
        or not fingerprint.regular_file
        or fingerprint.size is None
        or fingerprint.modified_ns is None
        or fingerprint.sha256 is None
    ):
        raise error
    return _StoredFile(
        filename=path.name,
        size=fingerprint.size,
        modified_ns=fingerprint.modified_ns,
        sha256=fingerprint.sha256,
    )


def _confined_file(directory: Path, filename: str) -> Path:
    _require_filename(filename, "restore")
    candidate = directory / filename
    if candidate.parent != directory:
        raise PendingRestoreCorruptError(
            "Restore metadata references a file outside its workspace."
        )
    return candidate


def _required_text(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> str:
    value = values[field]
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_LENGTH:
        raise error_type(f"Restore metadata field {field!r} is invalid.")
    return value


def _required_filename_field(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> str:
    value = _required_text(values, field, error_type)
    try:
        _require_filename(value, field)
    except RestoreVerificationError as error:
        raise error_type(str(error)) from error
    return value


def _optional_filename_field(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> str | None:
    value = values[field]
    if value is None:
        return None
    if not isinstance(value, str):
        raise error_type(f"Restore metadata field {field!r} is invalid.")
    return _required_filename_field(values, field, error_type)


def _required_backup_filename_field(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> str:
    value = _required_filename_field(values, field, error_type)
    if Path(value).suffix != BACKUP_EXTENSION:
        raise error_type("Restore backup filename has an invalid extension.")
    return value


def _require_filename(value: str, label: str) -> None:
    if (
        not value
        or len(value) > MAX_TEXT_LENGTH
        or Path(value).name != value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
    ):
        raise RestoreVerificationError(f"Restore {label} filename is invalid.")


def _required_uuid(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> UUID:
    value = _required_text(values, field, error_type)
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise error_type("Restore request identifier is invalid.") from error
    if str(parsed) != value:
        raise error_type("Restore request identifier is not canonical.")
    return parsed


def _required_int(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> int:
    value = values[field]
    if type(value) is not int:
        raise error_type(f"Restore metadata field {field!r} must be an integer.")
    return value


def _required_nonnegative_int(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> int:
    value = _required_int(values, field, error_type)
    if value < 0:
        raise error_type(f"Restore metadata field {field!r} is invalid.")
    return value


def _required_size(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> int:
    value = _required_int(values, field, error_type)
    if value <= 0 or value > MAX_DATABASE_BYTES:
        raise error_type(f"Restore metadata field {field!r} is invalid.")
    return value


def _required_sha256(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> str:
    value = _required_text(values, field, error_type)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise error_type(f"Restore metadata field {field!r} is invalid.")
    return value


def _required_backup_kind(
    values: Mapping[str, object],
    error_type: type[RestoreError],
) -> BackupKind:
    value = _required_text(values, "backup_kind", error_type)
    try:
        return BackupKind(value)
    except ValueError as error:
        raise error_type("Restore backup kind is invalid.") from error


def _required_utc(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> datetime:
    value = _required_text(values, field, error_type)
    if not value.endswith("Z"):
        raise error_type("Restore metadata timestamp must use UTC.")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise error_type("Restore metadata timestamp is invalid.") from error
    parsed = _require_utc(parsed, error_type)
    if _format_utc(parsed) != value:
        raise error_type("Restore metadata timestamp is not canonical UTC.")
    return parsed


def _require_utc(
    value: datetime,
    error_type: type[RestoreError],
) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise error_type("Restore operations require a timezone-aware UTC clock.")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Never:
    raise ValueError(f"Non-standard JSON constant {value!r}.")


def _remove_database(
    database: Path | None,
    error_type: type[RestoreError],
) -> None:
    if database is None:
        return
    for path in (database, *sqlite_sidecar_paths(database)):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise error_type("Restore transaction files could not be removed safely.") from error


def _remove_sidecars(
    database: Path,
    error_type: type[RestoreError],
) -> None:
    for path in sqlite_sidecar_paths(database):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise error_type("Restore database sidecars could not be removed safely.") from error


def _best_effort_remove_database(database: Path | None) -> None:
    if database is None:
        return
    for path in (database, *sqlite_sidecar_paths(database)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _set_private_permissions(path: Path, *, directory: bool = False) -> None:
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        if os.name != "nt":
            raise


def _remove_empty_workspace(workspace: Path) -> None:
    try:
        workspace.rmdir()
    except OSError:
        pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "OPERATION_FILENAME",
    "PENDING_FILENAME",
    "RESTORE_DIRECTORY_NAME",
    "SQLiteRestoreService",
    "StartupRestoreCoordinator",
    "UNVERSIONED_REVISION",
    "apply_pending_restore",
]
