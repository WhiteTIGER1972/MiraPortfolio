"""Integration tests for verified SQLite backup creation."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.application.backup import BackupKind
from app.core.exceptions import (
    BackupCreationError,
    BackupNotSupportedError,
    ConcurrentDatabaseChangeError,
)
from app.core.settings import Settings
from app.infrastructure.persistence import database_backup
from app.infrastructure.persistence.database_backup import (
    DATABASE_MEMBER,
    FORMAT_VERSION,
    MANIFEST_MEMBER,
    SQLiteBackupService,
)
from app.infrastructure.persistence.sqlalchemy.base import Base
from app.infrastructure.persistence.sqlalchemy.models import (
    AssetModel,
    PortfolioModel,
    PriceHistoryModel,
    TransactionModel,
)
from app.infrastructure.persistence.sqlite_validation import (
    FileFingerprint,
    SQLiteDatabaseFingerprint,
    fingerprint_sqlite_database,
    sqlite_file_path,
    validate_current_sqlite_database,
)

HEAD = "20260718_0001"
CREATED_AT = datetime(2026, 7, 30, 17, 25, tzinfo=UTC)


def extract_database(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(DATABASE_MEMBER) as source:
            with destination.open("xb") as output:
                shutil.copyfileobj(source, output)


def read_manifest(archive_path: Path) -> tuple[bytes, dict[str, object]]:
    with zipfile.ZipFile(archive_path) as archive:
        raw = archive.read(MANIFEST_MEMBER)
    parsed = json.loads(raw.decode("utf-8"))
    assert isinstance(parsed, dict)
    return raw, parsed


def archive_artifacts(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.name.startswith((".backup-", ".verify-"))
            or path.name.endswith(("-wal", "-shm", "-journal"))
        ),
        key=lambda path: path.name,
    )


def insert_representative_rows(database_url: str) -> tuple[str, str, str, str]:
    asset_id = uuid4()
    portfolio_id = uuid4()
    transaction_id = uuid4()
    price_id = uuid4()
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            session.add_all(
                (
                    AssetModel(
                        id=asset_id,
                        symbol="BACKUP",
                        name="Backup Asset",
                        asset_type="equity",
                        currency="TRY",
                        is_active=True,
                        created_at=CREATED_AT,
                    ),
                    PortfolioModel(
                        id=portfolio_id,
                        name="Backup Portfolio",
                        base_currency="TRY",
                        is_archived=False,
                        created_at=CREATED_AT,
                    ),
                    TransactionModel(
                        id=transaction_id,
                        portfolio_id=portfolio_id,
                        asset_id=asset_id,
                        position=0,
                        quantity=Decimal("2.5"),
                        price=Decimal("123.45"),
                        transaction_type="buy",
                        commission=Decimal("1.25"),
                        tax=Decimal("0"),
                        date=CREATED_AT,
                    ),
                    PriceHistoryModel(
                        id=price_id,
                        asset_id=asset_id,
                        price=Decimal("130.75"),
                        currency="TRY",
                        observed_at=CREATED_AT,
                    ),
                )
            )
            session.commit()
    finally:
        engine.dispose()
    return tuple(str(value) for value in (asset_id, portfolio_id, transaction_id, price_id))


def test_manual_backup_succeeds_with_strict_archive_and_manifest(
    backup_settings: Settings,
) -> None:
    service = SQLiteBackupService(backup_settings, clock=lambda: CREATED_AT)

    record = service.create_backup()

    assert record.path.exists()
    assert record.path.suffix == ".mirabackup"
    assert record.backup_kind is BackupKind.MANUAL
    assert record.created_at == CREATED_AT
    assert re.fullmatch(
        r"mira-portfolio-manual-20260730T172500Z-[0-9a-f]{12}\.mirabackup",
        record.filename,
    )
    with zipfile.ZipFile(record.path) as archive:
        assert archive.namelist() == [MANIFEST_MEMBER, DATABASE_MEMBER]
    raw, manifest = read_manifest(record.path)
    assert set(manifest) == {
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
    assert manifest == {
        "format_version": FORMAT_VERSION,
        "application_name": backup_settings.app_name,
        "application_version": backup_settings.app_version,
        "backup_kind": "manual",
        "created_at_utc": "2026-07-30T17:25:00.000000Z",
        "alembic_revision": HEAD,
        "database_filename": DATABASE_MEMBER,
        "database_size_bytes": record.database_size,
        "database_sha256": record.database_sha256,
    }
    assert raw == json.dumps(
        manifest,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    manifest_text = raw.decode("utf-8")
    assert backup_settings.database_url not in manifest_text
    assert str(backup_settings.database_path) not in manifest_text
    assert archive_artifacts(backup_settings.backup_directory) == []


def test_backup_payload_hash_size_revision_schema_and_integrity_are_verified(
    backup_settings: Settings,
    backup_service: SQLiteBackupService,
    tmp_path: Path,
) -> None:
    record = backup_service.create_backup()
    extracted = tmp_path / "verified-payload.sqlite"

    extract_database(record.path, extracted)

    payload = extracted.read_bytes()
    assert len(payload) == record.database_size
    assert hashlib.sha256(payload).hexdigest() == record.database_sha256
    assert validate_current_sqlite_database(extracted) == HEAD
    engine = create_engine(f"sqlite:///{extracted.as_posix()}")
    try:
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == HEAD
            assert set(inspect(connection).get_table_names()) - {"alembic_version"} == set(
                Base.metadata.tables
            )
            assert connection.exec_driver_sql("PRAGMA integrity_check").all() == [("ok",)]
    finally:
        engine.dispose()


def test_representative_rows_survive_backup(
    backup_settings: Settings,
    backup_service: SQLiteBackupService,
    tmp_path: Path,
) -> None:
    expected_ids = insert_representative_rows(backup_settings.database_url)
    record = backup_service.create_backup()
    extracted = tmp_path / "rows.sqlite"
    extract_database(record.path, extracted)
    engine = create_engine(f"sqlite:///{extracted.as_posix()}")
    try:
        with Session(engine) as session:
            assert str(session.scalar(select(AssetModel.id))) == expected_ids[0]
            assert str(session.scalar(select(PortfolioModel.id))) == expected_ids[1]
            assert str(session.scalar(select(TransactionModel.id))) == expected_ids[2]
            assert str(session.scalar(select(PriceHistoryModel.id))) == expected_ids[3]
            assert session.scalar(select(TransactionModel.quantity)) == Decimal("2.5")
            assert session.scalar(select(PriceHistoryModel.price)) == Decimal("130.75")
    finally:
        engine.dispose()


def test_wal_committed_rows_are_included_uncommitted_rows_are_excluded_and_source_is_unchanged(
    backup_settings: Settings,
    backup_service: SQLiteBackupService,
    tmp_path: Path,
) -> None:
    source = sqlite_file_path(backup_settings.database_url)
    with closing(sqlite3.connect(source)) as writer:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        writer.execute(
            "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                "COMMITTED",
                "Committed",
                "equity",
                "TRY",
                1,
                "2026-07-30T00:00:00.000000+00:00",
            ),
        )
        writer.commit()
        writer.execute("BEGIN")
        writer.execute(
            "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                "UNCOMMITTED",
                "Uncommitted",
                "equity",
                "TRY",
                1,
                "2026-07-30T00:00:00.000000+00:00",
            ),
        )
        before = fingerprint_sqlite_database(source)

        record = backup_service.create_backup()

        after = fingerprint_sqlite_database(source)
        assert after.database == before.database
        assert after.wal == before.wal
        assert after.journal == before.journal
        assert (
            after.shm.exists,
            after.shm.regular_file,
            after.shm.size,
            after.shm.modified_ns,
        ) == (
            before.shm.exists,
            before.shm.regular_file,
            before.shm.size,
            before.shm.modified_ns,
        )
        assert before.wal.exists
        assert before.shm.exists
        extracted = tmp_path / "wal.sqlite"
        extract_database(record.path, extracted)
        with closing(sqlite3.connect(extracted)) as copied:
            symbols = {row[0] for row in copied.execute("SELECT symbol FROM assets").fetchall()}
        assert "COMMITTED" in symbols
        assert "UNCOMMITTED" not in symbols
        writer.rollback()


def test_concurrent_source_change_fails_explicitly_and_cleans_staging(
    backup_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_copy = database_backup._copy_database

    def copy_then_change(source: Path, destination: Path) -> None:
        real_copy(source, destination)
        with sqlite3.connect(source) as connection:
            connection.execute(
                "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex,
                    "RACING",
                    "Concurrent",
                    "equity",
                    "TRY",
                    1,
                    "2026-07-30T00:00:00.000000+00:00",
                ),
            )

    monkeypatch.setattr(database_backup, "_copy_database", copy_then_change)

    with pytest.raises(ConcurrentDatabaseChangeError, match="changed while"):
        SQLiteBackupService(backup_settings).create_backup()

    assert list(backup_settings.backup_directory.glob("*.mirabackup")) == []
    assert archive_artifacts(backup_settings.backup_directory) == []


def test_partial_temporary_reservation_failure_removes_owned_file(
    backup_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_reserve = database_backup._reserve_temporary_file
    calls = 0

    def fail_second_reservation(
        directory: Path,
        *,
        prefix: str,
        suffix: str,
        verification: bool,
    ) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BackupCreationError("Temporary backup files could not be created.")
        return real_reserve(
            directory,
            prefix=prefix,
            suffix=suffix,
            verification=verification,
        )

    monkeypatch.setattr(
        database_backup,
        "_reserve_temporary_file",
        fail_second_reservation,
    )

    with pytest.raises(BackupCreationError, match="Temporary backup files"):
        SQLiteBackupService(backup_settings).create_backup()

    assert list(backup_settings.backup_directory.iterdir()) == []


def test_shm_reader_bytes_are_ignored_but_wal_or_shm_metadata_changes_are_not() -> None:
    database = FileFingerprint(True, True, 100, 1, "a" * 64)
    wal = FileFingerprint(True, True, 20, 2, "b" * 64)
    shm = FileFingerprint(True, True, 32_768, 3, "c" * 64)
    missing = FileFingerprint(False, False, None, None, None)
    before = SQLiteDatabaseFingerprint(database, wal, shm, missing)
    reader_only = SQLiteDatabaseFingerprint(
        database,
        wal,
        FileFingerprint(True, True, 32_768, 3, "d" * 64),
        missing,
    )
    changed_wal = SQLiteDatabaseFingerprint(
        database,
        FileFingerprint(True, True, 20, 2, "e" * 64),
        shm,
        missing,
    )
    changed_shm_metadata = SQLiteDatabaseFingerprint(
        database,
        wal,
        FileFingerprint(True, True, 32_768, 4, "c" * 64),
        missing,
    )

    assert database_backup._source_is_unchanged(before, reader_only)
    assert not database_backup._source_is_unchanged(before, changed_wal)
    assert not database_backup._source_is_unchanged(before, changed_shm_metadata)


def test_two_backups_in_same_second_are_unique_and_never_overwrite(
    backup_settings: Settings,
) -> None:
    service = SQLiteBackupService(backup_settings, clock=lambda: CREATED_AT)

    first = service.create_backup()
    first_bytes = first.path.read_bytes()
    second = service.create_backup()

    assert first.path != second.path
    assert first.filename != second.filename
    assert first.path.read_bytes() == first_bytes
    assert len(list(backup_settings.backup_directory.glob("*.mirabackup"))) == 2


def test_existing_final_backup_is_never_overwritten(
    backup_settings: Settings,
) -> None:
    backup_settings.backup_directory.mkdir()
    existing = (
        backup_settings.backup_directory
        / "mira-portfolio-manual-20260730T172500Z-000000000000.mirabackup"
    )
    existing.write_bytes(b"preserve this existing archive")
    service = SQLiteBackupService(
        backup_settings,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: UUID(int=0),
    )

    with pytest.raises(BackupCreationError, match="unique backup filename"):
        service.create_backup()

    assert existing.read_bytes() == b"preserve this existing archive"
    assert archive_artifacts(backup_settings.backup_directory) == []


def test_missing_source_fails_before_backup_directory_creation(tmp_path: Path) -> None:
    source = tmp_path / "missing.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{source.as_posix()}",
        backup_directory=tmp_path / "backups",
    )

    with pytest.raises(BackupCreationError, match="does not exist"):
        SQLiteBackupService(settings).create_backup()

    assert not settings.backup_directory.exists()


def test_directory_source_fails_safely(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    source.mkdir()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{source.as_posix()}",
        backup_directory=tmp_path / "backups",
    )

    with pytest.raises(BackupCreationError, match="not a regular file"):
        SQLiteBackupService(settings).create_backup()

    assert not settings.backup_directory.exists()


@pytest.mark.parametrize(
    "database_url",
    (
        "sqlite:///:memory:",
        "postgresql+psycopg://mira:secret@db.example/mira",
    ),
)
def test_unsupported_database_urls_affect_only_backup_operations(
    tmp_path: Path,
    database_url: str,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        backup_directory=tmp_path / "backups",
    )

    with pytest.raises(BackupNotSupportedError, match="file-based SQLite"):
        SQLiteBackupService(settings).create_backup()

    assert not settings.backup_directory.exists()


def test_uncurrent_source_is_rejected_and_all_temporary_files_are_removed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{source.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{source.as_posix()}",
        backup_directory=tmp_path / "backups",
    )

    with pytest.raises(BackupCreationError, match="must be current"):
        SQLiteBackupService(settings).create_backup()

    assert list(settings.backup_directory.iterdir()) == []
