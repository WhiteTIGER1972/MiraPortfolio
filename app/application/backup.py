"""Application-facing contracts and immutable results for database backups."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class BackupKind(StrEnum):
    """Identify why a backup was created."""

    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class BackupRecord:
    """Describe one fully verified backup archive."""

    path: Path
    filename: str
    created_at: datetime
    app_version: str
    alembic_revision: str
    database_size: int
    database_sha256: str
    backup_size: int
    backup_kind: BackupKind


@dataclass(frozen=True, slots=True)
class InvalidBackup:
    """Describe one invalid configured-directory entry without exposing internals."""

    filename: str
    reason: str


@dataclass(frozen=True, slots=True)
class BackupListing:
    """Return verified backups and sanitized invalid-entry summaries."""

    backups: tuple[BackupRecord, ...]
    invalid_backups: tuple[InvalidBackup, ...]


class BackupService(ABC):
    """Create, discover, and verify backups without exposing storage details."""

    @abstractmethod
    def create_backup(self, kind: BackupKind = BackupKind.MANUAL) -> BackupRecord:
        """Create and return one fully verified backup."""

    @abstractmethod
    def list_backups(self) -> BackupListing:
        """List verified backups and invalid configured-directory entries."""

    @abstractmethod
    def verify_backup(self, path: Path) -> BackupRecord:
        """Verify one archive without restoring it."""


__all__ = [
    "BackupKind",
    "BackupListing",
    "BackupRecord",
    "BackupService",
    "InvalidBackup",
]
