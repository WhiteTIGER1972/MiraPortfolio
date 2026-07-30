"""Application-facing contracts and immutable results for restart-safe restore."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from app.application.backup import BackupKind, BackupRecord


class RestoreOutcome(StrEnum):
    """Describe a durable restore lifecycle outcome."""

    STAGED = "staged"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    NO_PENDING_RESTORE = "no_pending_restore"
    RECOVERED_INTERRUPTED_OPERATION = "recovered_interrupted_operation"


@dataclass(frozen=True, slots=True)
class RestoreBackupIdentity:
    """Describe the verified backup payload without exposing its source path."""

    filename: str
    app_version: str
    alembic_revision: str
    database_size: int
    database_sha256: str
    backup_kind: BackupKind


@dataclass(frozen=True, slots=True)
class RestoreStageResult:
    """Describe one durable pending restore request."""

    request_id: UUID
    backup: RestoreBackupIdentity
    staged_at: datetime
    restart_required: bool
    outcome: RestoreOutcome


@dataclass(frozen=True, slots=True)
class RestoreApplicationResult:
    """Describe startup restore or interrupted-operation recovery."""

    request_id: UUID | None
    outcome: RestoreOutcome
    restored_backup: RestoreBackupIdentity | None
    pre_restore_backup: BackupRecord | None
    applied_at: datetime


class RestoreService(ABC):
    """Stage and inspect restart-required restores while the application runs."""

    @abstractmethod
    def stage_restore(self, backup_path: Path) -> RestoreStageResult:
        """Verify and stage a backup without modifying the active database."""

    @abstractmethod
    def get_pending_restore(self) -> RestoreStageResult | None:
        """Return the validated pending request, if one exists."""

    @abstractmethod
    def cancel_pending_restore(self) -> bool:
        """Cancel one validated pending request without touching active data."""


__all__ = [
    "RestoreApplicationResult",
    "RestoreBackupIdentity",
    "RestoreOutcome",
    "RestoreService",
    "RestoreStageResult",
]
