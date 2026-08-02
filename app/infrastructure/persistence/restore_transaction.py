"""Explicit offline restore transaction, rollback, and interruption recovery."""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from app.application.backup import BackupKind, BackupRecord, BackupService
from app.application.restore import (
    RestoreApplicationResult,
    RestoreBackupIdentity,
    RestoreOutcome,
)
from app.core.exceptions import (
    BackupError,
    DatabaseError,
    RestoreApplicationError,
    RestoreError,
    RestoreRecoveryError,
)
from app.core.settings import Settings
from app.infrastructure.persistence.restore_failpoints import (
    RestoreFailpoint,
    RestoreFailpointCallback,
    reach_failpoint,
)
from app.infrastructure.persistence.restore_metadata import (
    OriginalFiles,
    PendingRestore,
    RestoreOperation,
    StoredFile,
    load_operation,
    load_pending,
    replace_operation,
    require_matching_pending,
    require_utc,
    write_operation_exclusive,
)
from app.infrastructure.persistence.restore_staging import (
    final_identity,
    validate_staged_database,
)
from app.infrastructure.persistence.restore_state import (
    RestoreOperationState,
    validate_operation_transition,
)
from app.infrastructure.persistence.restore_workspace import (
    OPERATION_FILENAME,
    PENDING_FILENAME,
    OwnedDatabase,
    RestorePaths,
    flush_owned_database,
    is_link_like,
    path_present,
    remove_empty_workspace,
    remove_exact_metadata,
    remove_owned_database,
    remove_target_sidecars,
    require_clean_operation_workspace,
    require_clean_pending_workspace,
    require_no_orphan_artifacts,
    require_regular_owned_database,
    require_target_shape,
    rollback_database_path,
    staged_database_path,
    sync_directory_best_effort,
)
from app.infrastructure.persistence.sqlite_validation import (
    fingerprint_file,
    sqlite_sidecar_paths,
    validate_current_sqlite_database,
)


class RestoreTransactionManager:
    """Run and recover one durable offline database replacement transaction."""

    def __init__(
        self,
        settings: Settings,
        backup_service: BackupService,
        *,
        clock: Callable[[], datetime],
        script_location: Path | None,
        failpoint: RestoreFailpointCallback | None = None,
    ) -> None:
        self._settings = settings
        self._backup_service = backup_service
        self._clock = clock
        self._script_location = script_location
        self._failpoint = failpoint

    def apply_or_recover(
        self,
        paths: RestorePaths,
        now: datetime,
    ) -> RestoreApplicationResult:
        """Recover a journal when present, otherwise apply one pending request."""
        if path_present(paths.operation):
            return self.recover(paths, now)
        if not path_present(paths.pending):
            require_no_orphan_artifacts(paths)
            remove_empty_workspace(paths.workspace)
            return no_pending_result(now)

        pending = load_pending(paths)
        require_clean_pending_workspace(
            paths,
            request_id=pending.request_id,
            staged_database_filename=pending.staged_database_filename,
        )
        staged = validate_staged_database(
            paths,
            pending,
            script_location=self._script_location,
        )
        require_target_shape(paths.target)

        pre_restore = self._create_pre_restore_backup(paths)
        original_files = capture_original_files(paths.target)
        operation = operation_from_pending(
            pending,
            target_filename=paths.target.name,
            rollback_filename=f"rollback-{pending.request_id}.sqlite",
            pre_restore_backup_filename=(pre_restore.filename if pre_restore is not None else None),
            original_files=original_files,
            now=now,
        )
        write_operation_exclusive(paths, operation)
        reach_failpoint(
            self._failpoint,
            RestoreFailpoint.PREPARED_JOURNAL_PERSISTED,
        )

        try:
            if original_files[0] is not None:
                preserve_original(paths, operation)
                reach_failpoint(
                    self._failpoint,
                    RestoreFailpoint.ORIGINAL_TARGET_PRESERVED,
                )
            operation = self._transition(
                paths,
                operation,
                RestoreOperationState.ORIGINAL_PRESERVED,
            )
            reach_failpoint(
                self._failpoint,
                RestoreFailpoint.ORIGINAL_PRESERVED_JOURNAL_PERSISTED,
            )

            install_staged_database(paths, operation, staged)
            reach_failpoint(
                self._failpoint,
                RestoreFailpoint.RESTORED_TARGET_INSTALLED,
            )
            operation = self._transition(
                paths,
                operation,
                RestoreOperationState.RESTORED_INSTALLED,
            )
            reach_failpoint(
                self._failpoint,
                RestoreFailpoint.RESTORED_INSTALLED_JOURNAL_PERSISTED,
            )

            validate_installed_database(
                paths.target,
                operation,
                script_location=self._script_location,
            )
            reach_failpoint(
                self._failpoint,
                RestoreFailpoint.INSTALLED_TARGET_VALIDATED,
            )
            remove_target_sidecars(paths.target, RestoreApplicationError)
            operation = self._transition(
                paths,
                operation,
                RestoreOperationState.VERIFIED,
            )
            reach_failpoint(
                self._failpoint,
                RestoreFailpoint.VERIFIED_JOURNAL_PERSISTED,
            )
            finalize_success(paths, operation, failpoint=self._failpoint)
            return RestoreApplicationResult(
                request_id=pending.request_id,
                outcome=RestoreOutcome.APPLIED,
                restored_backup=final_identity(pending),
                pre_restore_backup=pre_restore,
                applied_at=require_utc(
                    self._clock(),
                    RestoreApplicationError,
                ),
            )
        except Exception as error:
            if operation.state is RestoreOperationState.VERIFIED:
                raise RestoreRecoveryError(
                    "The restored database is valid, but transaction cleanup is incomplete."
                ) from error
            try:
                rollback_operation(paths, operation)
            except Exception as recovery_error:
                raise RestoreRecoveryError(
                    "Database restore failed and automatic rollback could not complete."
                ) from recovery_error
            raise RestoreApplicationError(
                "Database restore failed; the original database was restored."
            ) from error

    def recover(
        self,
        paths: RestorePaths,
        now: datetime,
    ) -> RestoreApplicationResult:
        """Recover strictly from a durable journal and current filesystem evidence."""
        operation = load_operation(paths)
        load_matching_pending_if_present(paths, operation)
        staged = staged_database_path(
            paths,
            operation.request_id,
            operation.staged_database_filename,
        )
        rollback = rollback_database_path(
            paths,
            operation.request_id,
            operation.rollback_database_filename,
        )
        require_clean_operation_workspace(
            paths,
            request_id=operation.request_id,
            staged_database_filename=operation.staged_database_filename,
            rollback_database_filename=operation.rollback_database_filename,
        )
        _reject_link_like_evidence(paths, staged, rollback)

        if operation.state is RestoreOperationState.PREPARED:
            self._recover_prepared(paths, operation, staged, rollback)
            return recovery_result(
                operation,
                now,
                RestoreOutcome.RECOVERED_INTERRUPTED_OPERATION,
            )

        if operation.state is RestoreOperationState.ORIGINAL_PRESERVED:
            self._recover_original_preserved(paths, operation, staged, rollback)
            return recovery_result(operation, now, RestoreOutcome.ROLLED_BACK)

        if operation.state is RestoreOperationState.RESTORED_INSTALLED:
            return self._recover_installed(
                paths,
                operation,
                rollback,
                now,
                verified_state=False,
            )

        if operation.state is RestoreOperationState.VERIFIED:
            return self._recover_installed(
                paths,
                operation,
                rollback,
                now,
                verified_state=True,
            )

        raise RestoreRecoveryError("The restore operation state is not supported.")

    def _recover_prepared(
        self,
        paths: RestorePaths,
        operation: RestoreOperation,
        staged: OwnedDatabase,
        rollback: OwnedDatabase,
    ) -> None:
        original_expected = operation.original_files[0] is not None
        target_exists = path_present(paths.target)
        rollback_exists = path_present(rollback.path)
        if rollback_exists:
            if not original_expected or target_exists:
                raise RestoreRecoveryError(
                    "PREPARED restore evidence is ambiguous; no files were changed."
                )
            rollback_operation(paths, operation)
            return
        if target_exists:
            if not matches_original_files(paths.target, operation.original_files):
                raise RestoreRecoveryError(
                    "PREPARED restore target does not match the original fingerprint."
                )
            remove_operation_journal(paths)
            return
        if original_expected:
            raise RestoreRecoveryError(
                "PREPARED restore is missing both original and rollback databases."
            )
        if not path_present(staged.path):
            raise RestoreRecoveryError("PREPARED restore is missing its staged database.")
        remove_operation_journal(paths)

    def _recover_original_preserved(
        self,
        paths: RestorePaths,
        operation: RestoreOperation,
        staged: OwnedDatabase,
        rollback: OwnedDatabase,
    ) -> None:
        original_expected = operation.original_files[0] is not None
        if original_expected and not path_present(rollback.path):
            raise RestoreRecoveryError("The preserved original database is missing.")
        if path_present(paths.target) and path_present(staged.path):
            raise RestoreRecoveryError("ORIGINAL_PRESERVED restore evidence is ambiguous.")
        rollback_operation(paths, operation)

    def _recover_installed(
        self,
        paths: RestorePaths,
        operation: RestoreOperation,
        rollback: OwnedDatabase,
        now: datetime,
        *,
        verified_state: bool,
    ) -> RestoreApplicationResult:
        original_expected = operation.original_files[0] is not None
        rollback_exists = path_present(rollback.path)
        if not verified_state and original_expected and not rollback_exists:
            raise RestoreRecoveryError(
                "RESTORED_INSTALLED restore is missing its rollback database."
            )
        try:
            validate_installed_database(
                paths.target,
                operation,
                script_location=self._script_location,
            )
            pre_restore = self._recover_pre_restore_backup(operation)
        except (RestoreError, DatabaseError, OSError):
            if original_expected and not rollback_exists:
                raise RestoreRecoveryError(
                    "Interrupted restore validation failed and rollback evidence is missing."
                )
            try:
                rollback_operation(paths, operation)
            except Exception as recovery_error:
                raise RestoreRecoveryError(
                    "Interrupted restore validation and rollback both failed."
                ) from recovery_error
            return recovery_result(operation, now, RestoreOutcome.ROLLED_BACK)

        try:
            remove_target_sidecars(paths.target, RestoreRecoveryError)
            finalize_success(paths, operation, failpoint=self._failpoint)
        except (RestoreError, OSError) as error:
            raise RestoreRecoveryError(
                "The recovered database is valid, but transaction cleanup is incomplete."
            ) from error
        return RestoreApplicationResult(
            request_id=operation.request_id,
            outcome=RestoreOutcome.RECOVERED_INTERRUPTED_OPERATION,
            restored_backup=operation_identity(operation),
            pre_restore_backup=pre_restore,
            applied_at=now,
        )

    def _create_pre_restore_backup(
        self,
        paths: RestorePaths,
    ) -> BackupRecord | None:
        if not paths.target.exists():
            return None
        try:
            pre_restore = self._backup_service.create_backup(BackupKind.PRE_RESTORE)
            self._backup_service.verify_backup(pre_restore.path)
        except BackupError as error:
            raise RestoreApplicationError(
                "The pre-restore safety backup could not be created and verified."
            ) from error
        reach_failpoint(
            self._failpoint,
            RestoreFailpoint.PRE_RESTORE_BACKUP_CREATED,
        )
        return pre_restore

    def _recover_pre_restore_backup(
        self,
        operation: RestoreOperation,
    ) -> BackupRecord | None:
        filename = operation.pre_restore_backup_filename
        if filename is None:
            if operation.original_files[0] is not None:
                raise RestoreRecoveryError("The pre-restore safety backup identity is missing.")
            return None
        try:
            return self._backup_service.verify_backup(self._settings.backup_directory / filename)
        except BackupError as error:
            raise RestoreRecoveryError(
                "The pre-restore safety backup is missing or invalid."
            ) from error

    def _transition(
        self,
        paths: RestorePaths,
        operation: RestoreOperation,
        requested: RestoreOperationState,
    ) -> RestoreOperation:
        validate_operation_transition(operation.state, requested)
        transitioned = replace(
            operation,
            state=requested,
            updated_at=require_utc(
                self._clock(),
                RestoreApplicationError,
            ),
        )
        replace_operation(paths, transitioned)
        return transitioned


def capture_original_files(target: Path) -> OriginalFiles:
    """Fingerprint the target and every sidecar before any transaction mutation."""
    captured: list[StoredFile | None] = []
    for path in (target, *sqlite_sidecar_paths(target)):
        if is_link_like(path):
            raise RestoreApplicationError(
                "The database or a sidecar could not be fingerprinted safely."
            )
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
            StoredFile(
                filename=path.name,
                size=fingerprint.size,
                modified_ns=fingerprint.modified_ns,
                sha256=fingerprint.sha256,
            )
        )
    return (captured[0], captured[1], captured[2], captured[3])


def preserve_original(paths: RestorePaths, operation: RestoreOperation) -> None:
    """Move the exact original target and sidecars into request-owned rollback paths."""
    rollback = rollback_database_path(
        paths,
        operation.request_id,
        operation.rollback_database_filename,
    )
    if path_present(rollback.path) or any(
        path_present(path) for path in sqlite_sidecar_paths(rollback.path)
    ):
        raise RestoreRecoveryError("An unrelated rollback transaction file already exists.")
    if is_link_like(paths.target):
        raise RestoreRecoveryError("The original database target is link-like.")
    os.replace(paths.target, rollback.path)
    for source, destination in zip(
        sqlite_sidecar_paths(paths.target),
        sqlite_sidecar_paths(rollback.path),
        strict=True,
    ):
        if is_link_like(source) or is_link_like(destination):
            raise RestoreRecoveryError("A database sidecar is a link or reparse point.")
        if source.exists():
            os.replace(source, destination)
    sync_directory_best_effort(paths.target.parent)
    sync_directory_best_effort(paths.workspace)


def install_staged_database(
    paths: RestorePaths,
    operation: RestoreOperation,
    staged: OwnedDatabase,
) -> None:
    """Atomically install the closed staged file without overwriting evidence."""
    if path_present(paths.target):
        raise RestoreApplicationError(
            "The database target unexpectedly exists before restore installation."
        )
    expected = staged_database_path(
        paths,
        operation.request_id,
        operation.staged_database_filename,
    )
    if expected != staged:
        raise RestoreApplicationError("The staged database does not match the restore operation.")
    require_regular_owned_database(
        staged,
        RestoreApplicationError("The staged restore database is unavailable."),
    )
    flush_owned_database(staged, RestoreApplicationError)
    os.replace(staged.path, paths.target)
    sync_directory_best_effort(paths.target.parent)


def validate_installed_database(
    target: Path,
    operation: RestoreOperation,
    *,
    script_location: Path | None,
) -> None:
    """Validate the installed file against journal identity and current metadata."""
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
    if not hmac.compare_digest(
        revision,
        operation.expected_alembic_revision,
    ):
        raise RestoreApplicationError("The installed database revision is incorrect.")
    after = _required_regular_fingerprint(
        target,
        RestoreApplicationError("The installed restore database is unavailable."),
    )
    if after != fingerprint:
        raise RestoreApplicationError("The installed database changed during validation.")


def rollback_operation(
    paths: RestorePaths,
    operation: RestoreOperation,
) -> None:
    """Restore the exact original bytes or preserve all evidence on ambiguity."""
    staged = staged_database_path(
        paths,
        operation.request_id,
        operation.staged_database_filename,
    )
    rollback = rollback_database_path(
        paths,
        operation.request_id,
        operation.rollback_database_filename,
    )
    _reject_link_like_evidence(paths, staged, rollback)
    if operation.original_files[0] is not None:
        already_restored = _rollback_existing_original(
            paths,
            operation,
            staged,
            rollback,
        )
        if already_restored:
            return
    else:
        _rollback_without_original(paths, staged, rollback)

    sync_directory_best_effort(paths.target.parent)
    sync_directory_best_effort(paths.workspace)
    if not matches_original_files(paths.target, operation.original_files):
        raise RestoreRecoveryError("The original database fingerprint could not be restored.")
    remove_operation_journal(paths)


def _rollback_existing_original(
    paths: RestorePaths,
    operation: RestoreOperation,
    staged: OwnedDatabase,
    rollback: OwnedDatabase,
) -> bool:
    if not path_present(rollback.path) and matches_original_files(
        paths.target,
        operation.original_files,
    ):
        remove_operation_journal(paths)
        return True
    if not path_present(rollback.path):
        raise RestoreRecoveryError("The preserved original database is missing.")
    if path_present(paths.target):
        if path_present(staged.path):
            raise RestoreRecoveryError(
                "Both restored target and staged database exist; rollback is ambiguous."
            )
        _move_target_to_staged(paths, staged)
    os.replace(rollback.path, paths.target)
    _restore_rollback_sidecars(paths, rollback)
    return False


def _rollback_without_original(
    paths: RestorePaths,
    staged: OwnedDatabase,
    rollback: OwnedDatabase,
) -> None:
    if path_present(rollback.path):
        raise RestoreRecoveryError(
            "A rollback database exists for a restore with no original target."
        )
    if path_present(paths.target):
        if path_present(staged.path):
            raise RestoreRecoveryError(
                "Both restored target and staged database exist; recovery is ambiguous."
            )
        _move_target_to_staged(paths, staged)
    remove_target_sidecars(paths.target, RestoreRecoveryError)


def _restore_rollback_sidecars(
    paths: RestorePaths,
    rollback: OwnedDatabase,
) -> None:
    for destination, source in zip(
        sqlite_sidecar_paths(paths.target),
        sqlite_sidecar_paths(rollback.path),
        strict=True,
    ):
        if source.exists():
            if path_present(destination):
                raise RestoreRecoveryError(
                    "Conflicting original database sidecars make rollback ambiguous."
                )
            os.replace(source, destination)


def matches_original_files(
    target: Path,
    stored: OriginalFiles,
) -> bool:
    """Compare target and sidecars to the exact pre-transaction fingerprints."""
    for path, expected in zip(
        (target, *sqlite_sidecar_paths(target)),
        stored,
        strict=True,
    ):
        if is_link_like(path):
            return False
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


def finalize_success(
    paths: RestorePaths,
    operation: RestoreOperation,
    *,
    failpoint: RestoreFailpointCallback | None = None,
) -> None:
    """Remove exact owned evidence after a VERIFIED target, operation journal last."""
    staged = staged_database_path(
        paths,
        operation.request_id,
        operation.staged_database_filename,
    )
    rollback = rollback_database_path(
        paths,
        operation.request_id,
        operation.rollback_database_filename,
    )
    remove_exact_metadata(
        paths.pending,
        expected_name=PENDING_FILENAME,
        error_type=RestoreRecoveryError,
        missing_ok=True,
    )
    reach_failpoint(failpoint, RestoreFailpoint.PENDING_METADATA_REMOVED)
    remove_owned_database(staged, RestoreRecoveryError)
    reach_failpoint(failpoint, RestoreFailpoint.STAGED_DATABASE_CLEANED)
    remove_owned_database(rollback, RestoreRecoveryError)
    reach_failpoint(failpoint, RestoreFailpoint.ROLLBACK_DATABASE_CLEANED)
    remove_operation_journal(paths)
    reach_failpoint(failpoint, RestoreFailpoint.OPERATION_JOURNAL_CLEANED)
    remove_empty_workspace(paths.workspace)


def operation_from_pending(
    pending: PendingRestore,
    *,
    target_filename: str,
    rollback_filename: str,
    pre_restore_backup_filename: str | None,
    original_files: OriginalFiles,
    now: datetime,
) -> RestoreOperation:
    """Create the unchanged version-1 PREPARED journal model."""
    return RestoreOperation(
        request_id=pending.request_id,
        state=RestoreOperationState.PREPARED,
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


def load_matching_pending_if_present(
    paths: RestorePaths,
    operation: RestoreOperation,
) -> PendingRestore | None:
    """Load and match pending metadata when cleanup has not removed it yet."""
    if not path_present(paths.pending):
        return None
    pending = load_pending(paths)
    require_matching_pending(pending, operation)
    return pending


def operation_identity(
    operation: RestoreOperation,
) -> RestoreBackupIdentity:
    """Build the restored identity from a durable operation journal."""
    return RestoreBackupIdentity(
        filename=operation.source_backup_filename,
        app_version=operation.source_application_version,
        alembic_revision=operation.expected_alembic_revision,
        database_size=operation.expected_database_size,
        database_sha256=operation.expected_database_sha256,
        backup_kind=operation.backup_kind,
    )


def no_pending_result(now: datetime) -> RestoreApplicationResult:
    """Return the unchanged startup no-op result."""
    return RestoreApplicationResult(
        request_id=None,
        outcome=RestoreOutcome.NO_PENDING_RESTORE,
        restored_backup=None,
        pre_restore_backup=None,
        applied_at=now,
    )


def recovery_result(
    operation: RestoreOperation,
    now: datetime,
    outcome: RestoreOutcome,
) -> RestoreApplicationResult:
    """Return a rollback/interrupted-operation result."""
    return RestoreApplicationResult(
        request_id=operation.request_id,
        outcome=outcome,
        restored_backup=None,
        pre_restore_backup=None,
        applied_at=now,
    )


def remove_operation_journal(paths: RestorePaths) -> None:
    """Remove only the exact trusted operation journal."""
    remove_exact_metadata(
        paths.operation,
        expected_name=OPERATION_FILENAME,
        error_type=RestoreRecoveryError,
        missing_ok=True,
    )


def _move_target_to_staged(
    paths: RestorePaths,
    staged: OwnedDatabase,
) -> None:
    if is_link_like(paths.target) or is_link_like(staged.path):
        raise RestoreRecoveryError("Restore rollback encountered a link-like database artifact.")
    os.replace(paths.target, staged.path)
    for source, destination in zip(
        sqlite_sidecar_paths(paths.target),
        sqlite_sidecar_paths(staged.path),
        strict=True,
    ):
        if is_link_like(source) or is_link_like(destination):
            raise RestoreRecoveryError("Restore rollback encountered a link-like sidecar.")
        if source.exists():
            if path_present(destination):
                raise RestoreRecoveryError("Restore rollback sidecar evidence is ambiguous.")
            os.replace(source, destination)


def _reject_link_like_evidence(
    paths: RestorePaths,
    staged: OwnedDatabase,
    rollback: OwnedDatabase,
) -> None:
    for path in (
        paths.pending,
        paths.operation,
        staged.path,
        *sqlite_sidecar_paths(staged.path),
        rollback.path,
        *sqlite_sidecar_paths(rollback.path),
        paths.target,
        *sqlite_sidecar_paths(paths.target),
    ):
        if is_link_like(path):
            raise RestoreRecoveryError(
                "Restore recovery evidence contains a link or reparse point."
            )


def _required_regular_fingerprint(
    path: Path,
    error: RestoreError,
) -> StoredFile:
    if is_link_like(path):
        raise error
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
    return StoredFile(
        filename=path.name,
        size=fingerprint.size,
        modified_ns=fingerprint.modified_ns,
        sha256=fingerprint.sha256,
    )


__all__ = [
    "RestoreTransactionManager",
    "capture_original_files",
    "finalize_success",
    "install_staged_database",
    "load_matching_pending_if_present",
    "matches_original_files",
    "no_pending_result",
    "operation_from_pending",
    "operation_identity",
    "preserve_original",
    "recovery_result",
    "rollback_operation",
    "validate_installed_database",
]
