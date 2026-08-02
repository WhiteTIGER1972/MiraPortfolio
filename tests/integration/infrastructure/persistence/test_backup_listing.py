"""Integration tests for bounded, fully verified backup discovery."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import Settings
from app.infrastructure.persistence.database_backup import SQLiteBackupService


def test_listing_absent_directory_is_empty_and_does_not_create_it(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'unused.db').as_posix()}",
        backup_directory=tmp_path / "absent-backups",
    )

    listing = SQLiteBackupService(settings).list_backups()

    assert listing.backups == ()
    assert listing.invalid_backups == ()
    assert not settings.backup_directory.exists()


def test_listing_returns_only_fully_verified_backups_newest_first(
    backup_settings: Settings,
) -> None:
    older = SQLiteBackupService(
        backup_settings,
        clock=lambda: datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
    ).create_backup()
    newer = SQLiteBackupService(
        backup_settings,
        clock=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    ).create_backup()
    corrupt = backup_settings.backup_directory / "corrupt.mirabackup"
    corrupt.write_bytes(b"not a backup")
    directory_entry = backup_settings.backup_directory / "directory.mirabackup"
    directory_entry.mkdir()
    (backup_settings.backup_directory / "ignored.txt").write_text(
        "not considered",
        encoding="utf-8",
    )
    nested = backup_settings.backup_directory / "nested"
    nested.mkdir()
    shutil.copyfile(older.path, nested / "nested.mirabackup")

    listing = SQLiteBackupService(backup_settings).list_backups()

    assert listing.backups == (newer, older)
    assert tuple(item.filename for item in listing.invalid_backups) == (
        "corrupt.mirabackup",
        "directory.mirabackup",
    )
    assert all(item.reason for item in listing.invalid_backups)
    assert all(
        str(backup_settings.backup_directory) not in item.reason for item in listing.invalid_backups
    )
    assert list(backup_settings.backup_directory.glob(".verify-*")) == []


def test_listing_uses_filename_as_deterministic_timestamp_tie_breaker(
    backup_settings: Settings,
) -> None:
    created_at = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    service = SQLiteBackupService(backup_settings, clock=lambda: created_at)
    first = service.create_backup()
    second = service.create_backup()

    listing = service.list_backups()

    assert tuple(record.filename for record in listing.backups) == tuple(
        sorted((first.filename, second.filename))
    )
    assert listing.invalid_backups == ()
