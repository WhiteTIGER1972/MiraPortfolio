"""Internal deterministic interruption hooks for restore transaction tests."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum


class RestoreFailpoint(StrEnum):
    """Durable boundaries at which tests may simulate process termination."""

    STAGED_DATABASE_COMPLETED = "staged_database_completed"
    PENDING_METADATA_TEMPORARY_WRITTEN = "pending_metadata_temporary_written"
    PENDING_METADATA_REPLACED = "pending_metadata_replaced"
    PRE_RESTORE_BACKUP_CREATED = "pre_restore_backup_created"
    PREPARED_JOURNAL_PERSISTED = "prepared_journal_persisted"
    ORIGINAL_TARGET_PRESERVED = "original_target_preserved"
    ORIGINAL_PRESERVED_JOURNAL_PERSISTED = "original_preserved_journal_persisted"
    RESTORED_TARGET_INSTALLED = "restored_target_installed"
    RESTORED_INSTALLED_JOURNAL_PERSISTED = "restored_installed_journal_persisted"
    INSTALLED_TARGET_VALIDATED = "installed_target_validated"
    VERIFIED_JOURNAL_PERSISTED = "verified_journal_persisted"
    PENDING_METADATA_REMOVED = "pending_metadata_removed"
    STAGED_DATABASE_CLEANED = "staged_database_cleaned"
    ROLLBACK_DATABASE_CLEANED = "rollback_database_cleaned"
    OPERATION_JOURNAL_CLEANED = "operation_journal_cleaned"


RestoreFailpointCallback = Callable[[RestoreFailpoint], None]


def reach_failpoint(
    callback: RestoreFailpointCallback | None,
    point: RestoreFailpoint,
) -> None:
    """Invoke one test-only failpoint when an internal callback was supplied."""
    if callback is not None:
        callback(point)


__all__ = [
    "RestoreFailpoint",
    "RestoreFailpointCallback",
    "reach_failpoint",
]
