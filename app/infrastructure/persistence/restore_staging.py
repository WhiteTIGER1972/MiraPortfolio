"""Archive staging and pending-request lifecycle for restart-safe restores."""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.application.backup import BackupRecord
from app.application.restore import (
    RestoreBackupIdentity,
    RestoreOutcome,
    RestoreStageResult,
)
from app.core.exceptions import (
    BackupError,
    DatabaseError,
    RestoreError,
    RestoreRecoveryError,
    RestoreStagingError,
    RestoreVerificationError,
)
from app.core.settings import Settings
from app.infrastructure.persistence.database_backup import (
    extract_verified_backup_payload,
)
from app.infrastructure.persistence.database_preparation import (
    prepare_isolated_sqlite_database,
)
from app.infrastructure.persistence.restore_failpoints import (
    RestoreFailpoint,
    RestoreFailpointCallback,
    reach_failpoint,
)
from app.infrastructure.persistence.restore_metadata import (
    UNVERSIONED_REVISION,
    PendingRestore,
    StoredFile,
    load_pending,
    require_utc,
    write_pending,
)
from app.infrastructure.persistence.restore_workspace import (
    OwnedDatabase,
    RestorePaths,
    best_effort_remove_owned_database,
    cancellation_metadata_path,
    ensure_restore_workspace,
    flush_owned_database,
    is_link_like,
    path_present,
    remove_empty_workspace,
    remove_exact_metadata,
    remove_owned_database,
    remove_owned_sidecars,
    require_clean_pending_workspace,
    require_no_orphan_artifacts,
    require_no_pending_or_operation,
    require_regular_owned_database,
    require_restore_workspace,
    reserve_staging_database,
    set_private_permissions,
    staged_database_path,
    supported_restore_paths,
    sync_directory_best_effort,
)
from app.infrastructure.persistence.sqlite_validation import (
    fingerprint_file,
    inspect_sqlite_revision,
    sqlite_sidecar_paths,
    validate_current_sqlite_database,
)


class RestoreStagingManager:
    """Stage and manage one verified pending request without touching active data."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
        script_location: Path | None = None,
        failpoint: RestoreFailpointCallback | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or _utc_now
        self._uuid_factory = uuid_factory or uuid4
        self._script_location = script_location
        self._failpoint = failpoint

    def stage_restore(self, backup_path: Path) -> RestoreStageResult:
        """Stage one independently owned, current database for startup installation."""
        paths = supported_restore_paths(self._settings)
        ensure_restore_workspace(paths.workspace)
        require_no_pending_or_operation(paths)
        request_id = self._uuid_factory()
        staged_filename = f"staged-{request_id}.sqlite"
        staged = staged_database_path(paths, request_id, staged_filename)
        temporary: OwnedDatabase | None = None

        try:
            archive_before = _required_regular_fingerprint(
                backup_path,
                RestoreStagingError("The selected backup is not a regular file."),
            )
            temporary = reserve_staging_database(paths, request_id)
            backup = extract_verified_backup_payload(
                self._settings,
                backup_path,
                temporary.path,
            )
            archive_after = _required_regular_fingerprint(
                backup_path,
                RestoreStagingError("The selected backup became unavailable."),
            )
            if archive_before != archive_after:
                raise RestoreStagingError("The selected backup changed while it was being staged.")

            _validate_source_revision(
                temporary.path,
                backup,
                script_location=self._script_location,
            )
            prepare_isolated_sqlite_database(
                temporary.path,
                script_location=self._script_location,
            )
            head = validate_current_sqlite_database(
                temporary.path,
                script_location=self._script_location,
            )
            final_fingerprint = _required_regular_fingerprint(
                temporary.path,
                RestoreStagingError("The staged database is unavailable."),
            )
            remove_owned_sidecars(temporary, RestoreStagingError)
            flush_owned_database(temporary, RestoreStagingError)
            if path_present(staged.path):
                raise RestoreStagingError(
                    "A staged restore database already exists for this request."
                )
            os.replace(temporary.path, staged.path)
            sync_directory_best_effort(paths.workspace)
            temporary = None
            set_private_permissions(staged.path)
            reach_failpoint(
                self._failpoint,
                RestoreFailpoint.STAGED_DATABASE_COMPLETED,
            )

            pending = PendingRestore(
                request_id=request_id,
                created_at=require_utc(self._clock(), RestoreStagingError),
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
            write_pending(paths, pending, failpoint=self._failpoint)
            return stage_result(pending)
        except RestoreError:
            best_effort_remove_owned_database(temporary)
            best_effort_remove_owned_database(staged)
            remove_empty_workspace(paths.workspace)
            raise
        except (BackupError, DatabaseError, OSError) as error:
            best_effort_remove_owned_database(temporary)
            best_effort_remove_owned_database(staged)
            remove_empty_workspace(paths.workspace)
            raise RestoreStagingError(
                "The backup could not be staged safely for restart."
            ) from error

    def get_pending_restore(self) -> RestoreStageResult | None:
        """Return pending state only after its staged database is revalidated."""
        paths = supported_restore_paths(self._settings)
        if not path_present(paths.workspace):
            return None
        require_restore_workspace(paths.workspace)
        if path_present(paths.operation):
            raise RestoreVerificationError("A restore operation requires startup recovery.")
        if not path_present(paths.pending):
            require_no_orphan_artifacts(paths)
            return None
        pending = load_pending(paths)
        require_clean_pending_workspace(
            paths,
            request_id=pending.request_id,
            staged_database_filename=pending.staged_database_filename,
        )
        validate_staged_database(
            paths,
            pending,
            script_location=self._script_location,
        )
        return stage_result(pending)

    def cancel_pending_restore(self) -> bool:
        """Cancel only a fully validated request confined to its restore workspace."""
        paths = supported_restore_paths(self._settings)
        if not path_present(paths.workspace):
            return False
        require_restore_workspace(paths.workspace)
        if path_present(paths.operation):
            raise RestoreVerificationError(
                "A restore operation requires startup recovery before cancellation."
            )
        if not path_present(paths.pending):
            require_no_orphan_artifacts(paths)
            return False

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
        held = cancellation_metadata_path(paths, pending.request_id)
        if path_present(held):
            raise RestoreVerificationError("Pending restore cancellation evidence already exists.")
        try:
            os.replace(paths.pending, held)
            sync_directory_best_effort(paths.workspace)
            remove_owned_database(staged, RestoreVerificationError)
            remove_exact_metadata(
                held,
                expected_name=held.name,
                error_type=RestoreVerificationError,
                missing_ok=False,
            )
            remove_empty_workspace(paths.workspace)
            return True
        except OSError as error:
            if path_present(held) and not is_link_like(held) and not path_present(paths.pending):
                try:
                    os.replace(held, paths.pending)
                    sync_directory_best_effort(paths.workspace)
                except OSError as recovery_error:
                    raise RestoreRecoveryError(
                        "Pending restore cancellation could not be rolled back."
                    ) from recovery_error
            raise RestoreVerificationError(
                "Pending restore cancellation could not complete safely."
            ) from error


def validate_staged_database(
    paths: RestorePaths,
    pending: PendingRestore,
    *,
    script_location: Path | None,
) -> OwnedDatabase:
    """Validate the exact request-owned staged database without modifying it."""
    staged = staged_database_path(
        paths,
        pending.request_id,
        pending.staged_database_filename,
    )
    require_regular_owned_database(
        staged,
        RestoreVerificationError("The staged restore database is unavailable."),
    )
    if any(is_link_like(path) for path in sqlite_sidecar_paths(staged.path)):
        raise RestoreVerificationError("A staged restore sidecar is a link or reparse point.")
    fingerprint = _required_regular_fingerprint(
        staged.path,
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
            staged.path,
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
    remove_owned_sidecars(staged, RestoreVerificationError)
    return staged


def stage_result(pending: PendingRestore) -> RestoreStageResult:
    """Build the unchanged public staging result."""
    return RestoreStageResult(
        request_id=pending.request_id,
        backup=source_identity(pending),
        staged_at=pending.created_at,
        restart_required=True,
        outcome=RestoreOutcome.STAGED,
    )


def source_identity(pending: PendingRestore) -> RestoreBackupIdentity:
    """Build the source archive identity stored in pending metadata."""
    return RestoreBackupIdentity(
        filename=pending.source_backup_filename,
        app_version=pending.source_application_version,
        alembic_revision=pending.source_alembic_revision,
        database_size=pending.source_database_size,
        database_sha256=pending.source_database_sha256,
        backup_kind=pending.backup_kind,
    )


def final_identity(pending: PendingRestore) -> RestoreBackupIdentity:
    """Build the prepared staged database identity."""
    return RestoreBackupIdentity(
        filename=pending.source_backup_filename,
        app_version=pending.source_application_version,
        alembic_revision=pending.expected_alembic_revision,
        database_size=pending.expected_database_size,
        database_sha256=pending.expected_database_sha256,
        backup_kind=pending.backup_kind,
    )


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


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "RestoreStagingManager",
    "final_identity",
    "source_identity",
    "stage_result",
    "validate_staged_database",
]
