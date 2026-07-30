"""Tests for the application-facing backup contract."""

import inspect
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import get_type_hints

import pytest

from app.application.backup import (
    BackupKind,
    BackupListing,
    BackupRecord,
    BackupService,
    InvalidBackup,
)


def sample_record() -> BackupRecord:
    return BackupRecord(
        path=Path("sample.mirabackup"),
        filename="sample.mirabackup",
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        app_version="0.1.0",
        alembic_revision="20260718_0001",
        database_size=123,
        database_sha256="a" * 64,
        backup_size=99,
        backup_kind=BackupKind.MANUAL,
    )


def test_backup_record_is_exact_immutable_application_dto() -> None:
    record = sample_record()

    assert tuple(field.name for field in fields(BackupRecord)) == (
        "path",
        "filename",
        "created_at",
        "app_version",
        "alembic_revision",
        "database_size",
        "database_sha256",
        "backup_size",
        "backup_kind",
    )
    assert "__dict__" not in BackupRecord.__slots__
    with pytest.raises(FrozenInstanceError):
        record.filename = "replacement.mirabackup"


def test_backup_listing_reports_valid_and_invalid_entries_immutably() -> None:
    record = sample_record()
    invalid = InvalidBackup("broken.mirabackup", "Archive is invalid.")
    listing = BackupListing((record,), (invalid,))

    assert listing.backups == (record,)
    assert listing.invalid_backups == (invalid,)
    with pytest.raises(FrozenInstanceError):
        listing.backups = ()


def test_backup_service_has_only_narrow_abstract_operations() -> None:
    assert BackupService.__abstractmethods__ == {
        "create_backup",
        "list_backups",
        "verify_backup",
    }
    assert list(inspect.signature(BackupService.create_backup).parameters) == ["self", "kind"]
    assert list(inspect.signature(BackupService.list_backups).parameters) == ["self"]
    assert list(inspect.signature(BackupService.verify_backup).parameters) == ["self", "path"]
    assert get_type_hints(BackupService.verify_backup)["path"] is Path
    assert tuple(BackupKind) == (BackupKind.MANUAL, BackupKind.PRE_RESTORE)


def test_backup_contract_has_no_infrastructure_ui_or_domain_imports() -> None:
    source = Path(inspect.getsourcefile(BackupService) or "").read_text(encoding="utf-8")

    assert "app.infrastructure" not in source
    assert "app.ui" not in source
    assert "app.domain" not in source
    assert "sqlite3" not in source
    assert "sqlalchemy" not in source
