"""Integration tests for staged and startup-applied SQLite restores."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.application.backup import BackupKind, BackupRecord
from app.application.restore import RestoreOutcome
from app.core.exceptions import (
    BackupCreationError,
    PendingRestoreCorruptError,
    RestoreAlreadyPendingError,
    RestoreApplicationError,
    RestoreNotSupportedError,
    RestoreRecoveryError,
    RestoreStagingError,
)
from app.core.settings import Settings
from app.infrastructure.database import DatabaseManager
from app.infrastructure.persistence import database_restore
from app.infrastructure.persistence.alembic_support import create_alembic_config
from app.infrastructure.persistence.database_backup import (
    DATABASE_MEMBER,
    SQLiteBackupService,
)
from app.infrastructure.persistence.database_preparation import prepare_database
from app.infrastructure.persistence.database_restore import (
    PENDING_FILENAME,
    RESTORE_DIRECTORY_NAME,
    UNVERSIONED_REVISION,
    SQLiteRestoreService,
    StartupRestoreCoordinator,
)
from app.infrastructure.persistence.sqlalchemy.base import Base
from app.infrastructure.persistence.sqlalchemy.models import AssetModel
from app.infrastructure.persistence.sqlite_validation import (
    fingerprint_file,
    fingerprint_sqlite_database,
    validate_current_sqlite_database,
)

NOW = datetime(2026, 7, 30, 20, 0, tzinfo=UTC)
HEAD = "20260718_0001"


def make_settings(tmp_path: Path, name: str) -> Settings:
    root = tmp_path / name
    database_directory = root / "database"
    database_directory.mkdir(parents=True)
    database = database_directory / "portfolio.db"
    return Settings(
        _env_file=None,
        data_directory=root,
        database_directory=database_directory,
        database_path=database,
        database_url=f"sqlite:///{database.as_posix()}",
        backup_directory=root / "backups",
    )


def current_settings(tmp_path: Path, name: str) -> Settings:
    settings = make_settings(tmp_path, name)
    prepare_database(settings, legacy_search_directory=tmp_path)
    return settings


def insert_asset(settings: Settings, symbol: str) -> None:
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            session.add(
                AssetModel(
                    id=uuid4(),
                    symbol=symbol,
                    name=f"{symbol} Asset",
                    asset_type="equity",
                    currency="TRY",
                    is_active=True,
                    created_at=NOW,
                )
            )
            session.commit()
    finally:
        engine.dispose()


def asset_symbols(database: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with Session(engine) as session:
            return set(session.scalars(select(AssetModel.symbol)).all())
    finally:
        engine.dispose()


def create_source_backup(
    tmp_path: Path,
    *,
    symbol: str = "SOURCE",
) -> tuple[Settings, BackupRecord]:
    settings = current_settings(tmp_path, f"source-{symbol.lower()}")
    insert_asset(settings, symbol)
    return settings, SQLiteBackupService(settings, clock=lambda: NOW).create_backup()


def restore_workspace(settings: Settings) -> Path:
    return settings.database_path.parent / RESTORE_DIRECTORY_NAME


def extract_backup_database(backup: BackupRecord, destination: Path) -> None:
    with zipfile.ZipFile(backup.path) as archive:
        with archive.open(DATABASE_MEMBER) as source:
            with destination.open("xb") as output:
                shutil.copyfileobj(source, output)


def test_stage_current_backup_while_manager_active_requires_restart_and_owns_payload(
    tmp_path: Path,
) -> None:
    _, backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active")
    insert_asset(active, "ACTIVE")
    manager = DatabaseManager(active).initialize()
    try:
        before = fingerprint_sqlite_database(active.database_path)
        service = SQLiteRestoreService(active, clock=lambda: NOW)

        result = service.stage_restore(backup.path)

        after = fingerprint_sqlite_database(active.database_path)
        assert result.outcome is RestoreOutcome.STAGED
        assert result.restart_required is True
        assert manager.health_check()
        assert before == after
        assert not active.backup_directory.exists()
        backup.path.unlink()
        assert service.get_pending_restore() == result
        pending = (restore_workspace(active) / PENDING_FILENAME).read_text(encoding="utf-8")
        assert str(backup.path) not in pending
        assert active.database_url not in pending
        assert str(Path.home()) not in pending
    finally:
        manager.shutdown()


def test_only_one_pending_restore_is_permitted_and_cancellation_is_bounded(
    tmp_path: Path,
) -> None:
    _, first = create_source_backup(tmp_path, symbol="FIRST")
    _, second = create_source_backup(tmp_path, symbol="SECOND")
    active = current_settings(tmp_path, "active-cancel")
    insert_asset(active, "KEEP")
    active_before = fingerprint_file(active.database_path)
    service = SQLiteRestoreService(active)
    service.stage_restore(first.path)

    with pytest.raises(RestoreAlreadyPendingError, match="already pending"):
        service.stage_restore(second.path)

    backup_hashes = {
        first.path: fingerprint_file(first.path),
        second.path: fingerprint_file(second.path),
    }
    assert service.cancel_pending_restore()
    assert service.get_pending_restore() is None
    assert not restore_workspace(active).exists()
    assert fingerprint_file(active.database_path) == active_before
    assert {path: fingerprint_file(path) for path in backup_hashes} == backup_hashes
    assert not service.cancel_pending_restore()


@pytest.mark.parametrize(
    "database_url",
    (
        "sqlite:///:memory:",
        "postgresql+psycopg://user:secret@example.invalid/mira",
    ),
)
def test_unsupported_target_urls_affect_only_restore_operations(
    tmp_path: Path,
    database_url: str,
) -> None:
    _, backup = create_source_backup(tmp_path)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        database_directory=tmp_path / "database",
    )

    with pytest.raises(RestoreNotSupportedError, match="file-based SQLite"):
        SQLiteRestoreService(settings).stage_restore(backup.path)

    result = StartupRestoreCoordinator(settings).apply_pending_restore()
    assert result.outcome is RestoreOutcome.NO_PENDING_RESTORE


@pytest.mark.parametrize("payload", (b"not a zip", b"PK\x03\x04tampered"))
def test_invalid_or_tampered_archive_creates_no_pending_state(
    tmp_path: Path,
    payload: bytes,
) -> None:
    active = current_settings(tmp_path, "active-invalid")
    archive = tmp_path / "invalid.mirabackup"
    archive.write_bytes(payload)

    with pytest.raises(RestoreStagingError, match="could not be staged"):
        SQLiteRestoreService(active).stage_restore(archive)

    assert not restore_workspace(active).exists()
    assert fingerprint_file(active.database_path).exists


def test_pending_metadata_rejects_duplicate_keys_traversal_and_invalid_identifier(
    tmp_path: Path,
) -> None:
    _, backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active-metadata")
    service = SQLiteRestoreService(active)
    result = service.stage_restore(backup.path)
    pending_path = restore_workspace(active) / PENDING_FILENAME
    original = pending_path.read_bytes()

    pending_path.write_bytes(
        original.replace(
            b'{"backup_kind"',
            b'{"format_version":1,"backup_kind"',
            1,
        )
    )
    with pytest.raises(PendingRestoreCorruptError, match="strict JSON"):
        service.get_pending_restore()
    pending_path.write_bytes(original)

    parsed = json.loads(original)
    parsed["staged_database_filename"] = "../escape.sqlite"
    pending_path.write_text(json.dumps(parsed), encoding="utf-8")
    with pytest.raises(PendingRestoreCorruptError, match="filename"):
        service.cancel_pending_restore()
    pending_path.write_bytes(original)

    parsed = json.loads(original)
    parsed["request_id"] = str(result.request_id).upper()
    pending_path.write_text(json.dumps(parsed), encoding="utf-8")
    with pytest.raises(PendingRestoreCorruptError, match="canonical"):
        service.get_pending_restore()

    assert active.database_path.exists()
    assert any(restore_workspace(active).glob("staged-*.sqlite"))


def test_pending_metadata_rejects_nonstandard_json_constants(
    tmp_path: Path,
) -> None:
    _, backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active-nan")
    service = SQLiteRestoreService(active)
    service.stage_restore(backup.path)
    pending_path = restore_workspace(active) / PENDING_FILENAME
    original = pending_path.read_bytes()
    parsed = json.loads(original)
    declared_size = parsed["expected_database_size_bytes"]
    pending_path.write_bytes(
        original.replace(
            f'"expected_database_size_bytes":{declared_size}'.encode(),
            b'"expected_database_size_bytes":NaN',
        )
    )

    with pytest.raises(PendingRestoreCorruptError, match="strict JSON"):
        service.get_pending_restore()


def test_compatible_unversioned_archive_is_stamped_only_on_staged_copy(
    tmp_path: Path,
) -> None:
    active = current_settings(tmp_path, "active-unversioned")
    legacy = tmp_path / "legacy.sqlite"
    engine = create_engine(f"sqlite:///{legacy.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()
    before = fingerprint_file(legacy)
    archive = tmp_path / "legacy.mirabackup"
    create_test_archive(active, legacy, archive, revision=UNVERSIONED_REVISION)

    result = SQLiteRestoreService(active).stage_restore(archive)

    assert result.backup.alembic_revision == UNVERSIONED_REVISION
    assert fingerprint_file(legacy) == before
    staged = next(restore_workspace(active).glob("staged-*.sqlite"))
    assert validate_current_sqlite_database(staged) == HEAD


def test_older_known_revision_is_upgraded_only_on_isolated_staged_copy(
    tmp_path: Path,
) -> None:
    active = current_settings(tmp_path, "active-older")
    script_location = create_two_revision_script(tmp_path)
    older = tmp_path / "older.sqlite"
    config = create_alembic_config(
        f"sqlite:///{older.as_posix()}",
        script_location=script_location,
    )
    command.upgrade(config, "restore_old")
    before = fingerprint_file(older)
    archive = tmp_path / "older.mirabackup"
    create_test_archive(active, older, archive, revision="restore_old")

    result = SQLiteRestoreService(
        active,
        script_location=script_location,
    ).stage_restore(archive)

    assert result.backup.alembic_revision == "restore_old"
    assert fingerprint_file(older) == before
    staged = next(restore_workspace(active).glob("staged-*.sqlite"))
    assert (
        validate_current_sqlite_database(
            staged,
            script_location=script_location,
        )
        == "restore_head"
    )


def test_incompatible_unversioned_and_unknown_revision_archives_are_rejected(
    tmp_path: Path,
) -> None:
    active = current_settings(tmp_path, "active-reject")
    incompatible = tmp_path / "incompatible.sqlite"
    engine = create_engine(f"sqlite:///{incompatible.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE snapshots")
    engine.dispose()
    incompatible_archive = tmp_path / "incompatible.mirabackup"
    create_test_archive(
        active,
        incompatible,
        incompatible_archive,
        revision=UNVERSIONED_REVISION,
    )

    with pytest.raises(RestoreStagingError):
        SQLiteRestoreService(active).stage_restore(incompatible_archive)
    assert not restore_workspace(active).exists()

    unknown = tmp_path / "unknown.sqlite"
    shutil.copyfile(active.database_path, unknown)
    with closing(sqlite3.connect(unknown)) as connection:
        connection.execute(
            "UPDATE alembic_version SET version_num = ?",
            ("unknown_revision",),
        )
        connection.commit()
    unknown_archive = tmp_path / "unknown.mirabackup"
    create_test_archive(active, unknown, unknown_archive, revision="unknown_revision")

    with pytest.raises(RestoreStagingError, match="revision"):
        SQLiteRestoreService(active).stage_restore(unknown_archive)
    assert not restore_workspace(active).exists()


def test_startup_no_pending_restore_is_noop_without_workspace(
    tmp_path: Path,
) -> None:
    active = current_settings(tmp_path, "active-noop")
    before = fingerprint_file(active.database_path)

    result = StartupRestoreCoordinator(active, clock=lambda: NOW).apply_pending_restore()

    assert result.outcome is RestoreOutcome.NO_PENDING_RESTORE
    assert result.request_id is None
    assert fingerprint_file(active.database_path) == before
    assert not restore_workspace(active).exists()


def test_existing_target_is_restored_with_verified_pre_restore_backup(
    tmp_path: Path,
) -> None:
    _, source_backup = create_source_backup(tmp_path, symbol="RESTORED")
    active = current_settings(tmp_path, "active-apply")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active, clock=lambda: NOW).stage_restore(source_backup.path)

    result = StartupRestoreCoordinator(
        active,
        clock=lambda: NOW,
    ).apply_pending_restore()

    assert result.outcome is RestoreOutcome.APPLIED
    assert asset_symbols(active.database_path) == {"RESTORED"}
    installed = fingerprint_file(active.database_path)
    assert result.restored_backup is not None
    assert result.restored_backup.database_size == installed.size
    assert result.restored_backup.database_sha256 == installed.sha256
    assert result.restored_backup.alembic_revision == HEAD
    assert result.pre_restore_backup is not None
    assert result.pre_restore_backup.backup_kind is BackupKind.PRE_RESTORE
    verified = SQLiteBackupService(active).verify_backup(result.pre_restore_backup.path)
    assert verified == result.pre_restore_backup
    extracted = tmp_path / "pre-restore.sqlite"
    extract_backup_database(result.pre_restore_backup, extracted)
    assert asset_symbols(extracted) == {"ORIGINAL"}
    assert not restore_workspace(active).exists()


def test_existing_crash_left_wal_state_is_captured_before_restore(
    tmp_path: Path,
) -> None:
    _, source_backup = create_source_backup(tmp_path, symbol="RESTORED")
    active = current_settings(tmp_path, "active-wal")
    child = "\n".join(
        (
            "import os, sqlite3, sys, uuid",
            "connection = sqlite3.connect(sys.argv[1])",
            "connection.execute('PRAGMA journal_mode = WAL')",
            "connection.execute('PRAGMA wal_autocheckpoint = 0')",
            "connection.execute(",
            "    'INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)',",
            "    (uuid.uuid4().hex, 'WALOLD', 'WAL Old', 'equity', 'TRY', 1,",
            "     '2026-07-30T20:00:00.000000+00:00'),",
            ")",
            "connection.commit()",
            "os._exit(0)",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", child, str(active.database_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    before = fingerprint_sqlite_database(active.database_path)
    assert before.wal.exists
    assert before.shm.exists
    SQLiteRestoreService(active).stage_restore(source_backup.path)

    result = StartupRestoreCoordinator(active).apply_pending_restore()

    assert result.outcome is RestoreOutcome.APPLIED
    assert asset_symbols(active.database_path) == {"RESTORED"}
    assert result.pre_restore_backup is not None
    extracted = tmp_path / "pre-restore-wal.sqlite"
    extract_backup_database(result.pre_restore_backup, extracted)
    assert asset_symbols(extracted) == {"WALOLD"}
    assert not restore_workspace(active).exists()


def test_missing_target_applies_without_pre_restore_backup(
    tmp_path: Path,
) -> None:
    _, source_backup = create_source_backup(tmp_path, symbol="NEW")
    active = current_settings(tmp_path, "active-missing")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    active.database_path.unlink()

    result = StartupRestoreCoordinator(active).apply_pending_restore()

    assert result.outcome is RestoreOutcome.APPLIED
    assert result.pre_restore_backup is None
    assert asset_symbols(active.database_path) == {"NEW"}
    assert not active.backup_directory.exists()


def test_pre_restore_backup_failure_preserves_target_and_pending_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source_backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active-backup-failure")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    before = fingerprint_sqlite_database(active.database_path)

    def fail_backup(
        self: SQLiteBackupService,
        kind: BackupKind = BackupKind.MANUAL,
    ) -> BackupRecord:
        del self, kind
        raise BackupCreationError("injected pre-restore failure")

    monkeypatch.setattr(SQLiteBackupService, "create_backup", fail_backup)

    with pytest.raises(RestoreApplicationError, match="safety backup"):
        StartupRestoreCoordinator(active).apply_pending_restore()

    assert fingerprint_sqlite_database(active.database_path) == before
    assert (restore_workspace(active) / PENDING_FILENAME).exists()
    assert any(restore_workspace(active).glob("staged-*.sqlite"))


@pytest.mark.parametrize("failure_point", ("install", "validation"))
def test_install_or_post_validation_failure_restores_original_byte_for_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    _, source_backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, f"active-{failure_point}")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    before = fingerprint_sqlite_database(active.database_path)

    if failure_point == "install":
        real_replace = database_restore.os.replace

        def fail_install(source: Path, destination: Path) -> None:
            if source.name.startswith("staged-") and destination == active.database_path:
                raise PermissionError("simulated locked destination")
            real_replace(source, destination)

        monkeypatch.setattr(database_restore.os, "replace", fail_install)
    else:
        monkeypatch.setattr(
            database_restore,
            "_validate_installed_database",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RestoreApplicationError("injected validation failure")
            ),
        )

    with pytest.raises(RestoreApplicationError, match="original database was restored"):
        StartupRestoreCoordinator(active).apply_pending_restore()

    assert fingerprint_sqlite_database(active.database_path) == before
    assert asset_symbols(active.database_path) == {"ORIGINAL"}
    assert (restore_workspace(active) / PENDING_FILENAME).exists()
    assert any(restore_workspace(active).glob("staged-*.sqlite"))
    assert not (restore_workspace(active) / "operation.json").exists()
    assert list(restore_workspace(active).glob("rollback-*")) == []
    retained = list(active.backup_directory.glob("*.mirabackup"))
    assert len(retained) == 1
    assert (
        SQLiteBackupService(active).verify_backup(retained[0]).backup_kind is BackupKind.PRE_RESTORE
    )


def test_rollback_failure_preserves_transaction_artifacts_and_aborts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source_backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active-rollback-failure")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    real_replace = database_restore.os.replace

    def fail_install_and_rollback(source: Path, destination: Path) -> None:
        if source.name.startswith("staged-") and destination == active.database_path:
            raise PermissionError("injected install failure")
        if source.name.startswith("rollback-") and destination == active.database_path:
            raise PermissionError("injected rollback failure")
        real_replace(source, destination)

    monkeypatch.setattr(database_restore.os, "replace", fail_install_and_rollback)

    with pytest.raises(RestoreRecoveryError, match="rollback could not complete"):
        StartupRestoreCoordinator(active).apply_pending_restore()

    workspace = restore_workspace(active)
    assert (workspace / "operation.json").exists()
    assert any(workspace.glob("rollback-*.sqlite"))
    assert any(workspace.glob("staged-*.sqlite"))


def test_locked_target_simulation_aborts_before_replacement_and_preserves_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source_backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active-locked-target")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    before = fingerprint_file(active.database_path)
    real_replace = database_restore.os.replace

    def fail_target_move(source: Path, destination: Path) -> None:
        if source == active.database_path and destination.name.startswith("rollback-"):
            raise PermissionError("simulated Windows file lock")
        real_replace(source, destination)

    monkeypatch.setattr(database_restore.os, "replace", fail_target_move)

    with pytest.raises(RestoreApplicationError, match="original database was restored"):
        StartupRestoreCoordinator(active).apply_pending_restore()

    assert fingerprint_file(active.database_path) == before
    workspace = restore_workspace(active)
    assert (workspace / PENDING_FILENAME).exists()
    assert any(workspace.glob("staged-*.sqlite"))
    assert not (workspace / "operation.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only file semantics")
def test_read_only_target_aborts_before_replacement_and_preserves_original(
    tmp_path: Path,
) -> None:
    _, source_backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active-read-only")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    before = fingerprint_file(active.database_path)
    active.database_path.chmod(stat.S_IREAD)
    try:
        with pytest.raises(RestoreApplicationError, match="not writable"):
            StartupRestoreCoordinator(active).apply_pending_restore()
        assert fingerprint_file(active.database_path) == before
        assert (restore_workspace(active) / PENDING_FILENAME).exists()
        assert not active.backup_directory.exists()
    finally:
        active.database_path.chmod(stat.S_IWRITE | stat.S_IREAD)


def test_unrelated_existing_rollback_artifact_is_never_overwritten_or_deleted(
    tmp_path: Path,
) -> None:
    _, source_backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active-existing-rollback")
    insert_asset(active, "ORIGINAL")
    staged = SQLiteRestoreService(active).stage_restore(source_backup.path)
    workspace = restore_workspace(active)
    unrelated = workspace / f"rollback-{staged.request_id}.sqlite"
    unrelated.write_bytes(b"unrelated rollback data")
    before = fingerprint_file(active.database_path)

    with pytest.raises(PendingRestoreCorruptError, match="Unreferenced"):
        StartupRestoreCoordinator(active).apply_pending_restore()

    assert fingerprint_file(active.database_path) == before
    assert unrelated.read_bytes() == b"unrelated rollback data"
    assert not (workspace / "operation.json").exists()


def test_prepared_interruption_restores_original_then_next_start_applies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source_backup = create_source_backup(tmp_path, symbol="RESTORED")
    active = current_settings(tmp_path, "active-prepared-interruption")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    real_replace = database_restore.os.replace
    interrupted = False

    def interrupt_after_preserve(source: Path, destination: Path) -> None:
        nonlocal interrupted
        real_replace(source, destination)
        if destination.name.startswith("rollback-") and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(database_restore.os, "replace", interrupt_after_preserve)
    with pytest.raises(KeyboardInterrupt):
        StartupRestoreCoordinator(active).apply_pending_restore()
    monkeypatch.setattr(database_restore.os, "replace", real_replace)

    recovered = StartupRestoreCoordinator(active).apply_pending_restore()
    assert recovered.outcome is RestoreOutcome.RECOVERED_INTERRUPTED_OPERATION
    assert asset_symbols(active.database_path) == {"ORIGINAL"}
    assert (restore_workspace(active) / PENDING_FILENAME).exists()
    assert not (restore_workspace(active) / "operation.json").exists()

    applied = StartupRestoreCoordinator(active).apply_pending_restore()
    assert applied.outcome is RestoreOutcome.APPLIED
    assert asset_symbols(active.database_path) == {"RESTORED"}
    assert not restore_workspace(active).exists()


def test_original_preserved_interruption_rolls_back_deterministically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source_backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active-preserved-interruption")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    real_replace = database_restore.os.replace

    def interrupt_before_install(source: Path, destination: Path) -> None:
        if source.name.startswith("staged-") and destination == active.database_path:
            raise KeyboardInterrupt
        real_replace(source, destination)

    monkeypatch.setattr(database_restore.os, "replace", interrupt_before_install)
    with pytest.raises(KeyboardInterrupt):
        StartupRestoreCoordinator(active).apply_pending_restore()
    monkeypatch.setattr(database_restore.os, "replace", real_replace)

    recovered = StartupRestoreCoordinator(active).apply_pending_restore()

    assert recovered.outcome is RestoreOutcome.ROLLED_BACK
    assert asset_symbols(active.database_path) == {"ORIGINAL"}
    assert (restore_workspace(active) / PENDING_FILENAME).exists()
    assert any(restore_workspace(active).glob("staged-*.sqlite"))
    assert not (restore_workspace(active) / "operation.json").exists()


def test_restored_installed_interruption_with_valid_target_finalizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source_backup = create_source_backup(tmp_path, symbol="RESTORED")
    active = current_settings(tmp_path, "active-installed-interruption")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    real_validate = database_restore._validate_installed_database

    monkeypatch.setattr(
        database_restore,
        "_validate_installed_database",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        StartupRestoreCoordinator(active).apply_pending_restore()
    monkeypatch.setattr(
        database_restore,
        "_validate_installed_database",
        real_validate,
    )

    recovered = StartupRestoreCoordinator(active).apply_pending_restore()

    assert recovered.outcome is RestoreOutcome.RECOVERED_INTERRUPTED_OPERATION
    assert recovered.pre_restore_backup is not None
    assert asset_symbols(active.database_path) == {"RESTORED"}
    assert not restore_workspace(active).exists()
    assert (
        StartupRestoreCoordinator(active).apply_pending_restore().outcome
        is RestoreOutcome.NO_PENDING_RESTORE
    )


def test_verified_interruption_finalizes_without_rolling_back_valid_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source_backup = create_source_backup(tmp_path, symbol="VERIFIED")
    active = current_settings(tmp_path, "active-verified-interruption")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    real_finalize = database_restore._finalize_success
    monkeypatch.setattr(
        database_restore,
        "_finalize_success",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        StartupRestoreCoordinator(active).apply_pending_restore()
    monkeypatch.setattr(database_restore, "_finalize_success", real_finalize)

    recovered = StartupRestoreCoordinator(active).apply_pending_restore()

    assert recovered.outcome is RestoreOutcome.RECOVERED_INTERRUPTED_OPERATION
    assert asset_symbols(active.database_path) == {"VERIFIED"}
    assert not restore_workspace(active).exists()


def test_restored_installed_interruption_with_invalid_target_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source_backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active-invalid-installed")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    real_validate = database_restore._validate_installed_database
    monkeypatch.setattr(
        database_restore,
        "_validate_installed_database",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        StartupRestoreCoordinator(active).apply_pending_restore()
    monkeypatch.setattr(
        database_restore,
        "_validate_installed_database",
        real_validate,
    )
    active.database_path.write_bytes(b"corrupt restored target")

    recovered = StartupRestoreCoordinator(active).apply_pending_restore()

    assert recovered.outcome is RestoreOutcome.ROLLED_BACK
    assert asset_symbols(active.database_path) == {"ORIGINAL"}
    assert (restore_workspace(active) / PENDING_FILENAME).exists()
    assert not (restore_workspace(active) / "operation.json").exists()


def test_corrupt_journal_and_orphan_rollback_fail_closed(
    tmp_path: Path,
) -> None:
    active = current_settings(tmp_path, "active-corrupt-journal")
    workspace = restore_workspace(active)
    workspace.mkdir()
    (workspace / "operation.json").write_bytes(b"{invalid")

    with pytest.raises(RestoreRecoveryError, match="strict JSON"):
        StartupRestoreCoordinator(active).apply_pending_restore()

    (workspace / "operation.json").unlink()
    orphan = workspace / f"rollback-{uuid4()}.sqlite"
    orphan.write_bytes(b"do not guess")
    before = orphan.read_bytes()

    with pytest.raises(PendingRestoreCorruptError, match="Unreferenced"):
        StartupRestoreCoordinator(active).apply_pending_restore()

    assert orphan.read_bytes() == before
    assert active.database_path.exists()


def test_operation_journal_filename_traversal_is_rejected_before_file_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source_backup = create_source_backup(tmp_path)
    active = current_settings(tmp_path, "active-journal-traversal")
    insert_asset(active, "ORIGINAL")
    SQLiteRestoreService(active).stage_restore(source_backup.path)
    before = fingerprint_file(active.database_path)
    monkeypatch.setattr(
        database_restore,
        "_preserve_original",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        StartupRestoreCoordinator(active).apply_pending_restore()
    operation = restore_workspace(active) / "operation.json"
    parsed = json.loads(operation.read_bytes())
    parsed["rollback_database_filename"] = "../outside.sqlite"
    operation.write_text(json.dumps(parsed), encoding="utf-8")

    with pytest.raises(RestoreRecoveryError, match="filename"):
        StartupRestoreCoordinator(active).apply_pending_restore()

    assert fingerprint_file(active.database_path) == before
    assert not (active.database_path.parent / "outside.sqlite").exists()


def create_test_archive(
    settings: Settings,
    database: Path,
    archive: Path,
    *,
    revision: str,
) -> None:
    """Create a strict test archive around an explicitly generated temporary DB."""
    from app.infrastructure.persistence import database_backup

    fingerprint = fingerprint_file(database)
    assert fingerprint.size is not None
    assert fingerprint.sha256 is not None
    manifest = database_backup._BackupManifest(
        format_version=database_backup.FORMAT_VERSION,
        application_name=settings.app_name,
        application_version=settings.app_version,
        backup_kind=BackupKind.MANUAL,
        created_at=NOW,
        alembic_revision=revision,
        database_filename=DATABASE_MEMBER,
        database_size=fingerprint.size,
        database_sha256=fingerprint.sha256,
    )
    database_backup._write_archive(archive, database, manifest)


def create_two_revision_script(tmp_path: Path) -> Path:
    project_root = Path(__file__).resolve().parents[4]
    destination = tmp_path / "two-revision-migrations"
    shutil.copytree(project_root / "migrations", destination)
    initial = next((destination / "versions").glob("*.py"))
    source = initial.read_text(encoding="utf-8")
    source = source.replace(
        'revision: str = "20260718_0001"',
        'revision: str = "restore_old"',
    )
    initial.write_text(source, encoding="utf-8")
    (destination / "versions" / "restore_head.py").write_text(
        "\n".join(
            (
                '"""No-op test head after the complete old schema."""',
                "from typing import Sequence, Union",
                "revision: str = 'restore_head'",
                "down_revision: Union[str, Sequence[str], None] = 'restore_old'",
                "branch_labels: Union[str, Sequence[str], None] = None",
                "depends_on: Union[str, Sequence[str], None] = None",
                "def upgrade() -> None:",
                "    pass",
                "def downgrade() -> None:",
                "    pass",
                "",
            )
        ),
        encoding="utf-8",
    )
    return destination
