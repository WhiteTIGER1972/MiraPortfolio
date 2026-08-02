"""Strict metadata format and durable-write tests for database restores."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.application.backup import BackupKind
from app.core.exceptions import RestoreStagingError
from app.infrastructure.persistence import restore_metadata, restore_workspace
from app.infrastructure.persistence.restore_failpoints import RestoreFailpoint
from app.infrastructure.persistence.restore_metadata import (
    OPERATION_FIELDS,
    OPERATION_FORMAT_VERSION,
    PENDING_FIELDS,
    PENDING_FORMAT_VERSION,
    PendingRestore,
    RestoreOperation,
    load_operation,
    load_pending,
    operation_payload,
    pending_payload,
    write_operation_exclusive,
    write_pending,
)
from app.infrastructure.persistence.restore_state import RestoreOperationState
from app.infrastructure.persistence.restore_workspace import RestorePaths

REQUEST_ID = UUID("12345678-1234-5678-9234-567812345678")
NOW = datetime(2026, 7, 30, 20, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64


class SimulatedInterruption(BaseException):
    """Model termination at a durable metadata boundary."""


def make_paths(tmp_path: Path) -> RestorePaths:
    database_directory = tmp_path / "database"
    database_directory.mkdir()
    workspace = database_directory / "restore"
    workspace.mkdir()
    return RestorePaths(
        target=database_directory / "portfolio.db",
        workspace=workspace,
        pending=workspace / "pending.json",
        operation=workspace / "operation.json",
    )


def pending() -> PendingRestore:
    return PendingRestore(
        request_id=REQUEST_ID,
        created_at=NOW,
        staged_database_filename=f"staged-{REQUEST_ID}.sqlite",
        source_backup_filename="source.mirabackup",
        source_backup_sha256=SHA_A,
        source_application_version="0.1.0",
        source_alembic_revision="20260718_0001",
        source_database_size=4096,
        source_database_sha256=SHA_B,
        backup_kind=BackupKind.MANUAL,
        expected_database_size=8192,
        expected_database_sha256=SHA_A,
        expected_alembic_revision="20260718_0001",
    )


def operation() -> RestoreOperation:
    return RestoreOperation(
        request_id=REQUEST_ID,
        state=RestoreOperationState.PREPARED,
        created_at=NOW,
        updated_at=NOW,
        target_database_filename="portfolio.db",
        staged_database_filename=f"staged-{REQUEST_ID}.sqlite",
        rollback_database_filename=f"rollback-{REQUEST_ID}.sqlite",
        pre_restore_backup_filename="safety.mirabackup",
        source_backup_filename="source.mirabackup",
        source_application_version="0.1.0",
        source_alembic_revision="20260718_0001",
        source_database_size=4096,
        source_database_sha256=SHA_B,
        backup_kind=BackupKind.MANUAL,
        expected_database_size=8192,
        expected_database_sha256=SHA_A,
        expected_alembic_revision="20260718_0001",
        original_files=(None, None, None, None),
    )


def test_pending_format_v1_fields_and_round_trip_remain_exact(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    model = pending()

    assert PENDING_FORMAT_VERSION == 1
    assert set(pending_payload(model)) == PENDING_FIELDS

    write_pending(paths, model)

    assert load_pending(paths) == model
    assert paths.pending.read_bytes().endswith(b"}")
    assert b"\n" not in paths.pending.read_bytes()


def test_operation_format_v1_fields_and_round_trip_remain_exact(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    model = operation()

    assert OPERATION_FORMAT_VERSION == 1
    assert set(operation_payload(model)) == OPERATION_FIELDS

    write_operation_exclusive(paths, model)

    assert load_operation(paths) == model
    assert paths.operation.read_bytes().endswith(b"}")
    assert b"\n" not in paths.operation.read_bytes()


def test_durable_write_fsyncs_file_then_atomically_replaces_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = make_paths(tmp_path)
    fsync_calls: list[int] = []
    replace_destinations: list[Path] = []
    real_fsync = restore_metadata.os.fsync
    real_replace = restore_metadata.os.replace

    def record_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        real_fsync(descriptor)

    def record_replace(source: Path, destination: Path) -> None:
        replace_destinations.append(Path(destination))
        real_replace(source, destination)

    monkeypatch.setattr(restore_metadata.os, "fsync", record_fsync)
    monkeypatch.setattr(restore_metadata.os, "replace", record_replace)

    write_pending(paths, pending())

    assert fsync_calls
    assert replace_destinations == [paths.pending]
    assert load_pending(paths) == pending()


def test_file_fsync_failure_removes_temporary_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = make_paths(tmp_path)

    def fail_fsync(descriptor: int) -> None:
        del descriptor
        raise OSError("injected fsync failure")

    monkeypatch.setattr(restore_metadata.os, "fsync", fail_fsync)

    with pytest.raises(RestoreStagingError, match="written safely"):
        write_pending(paths, pending())

    assert not paths.pending.exists()
    assert list(paths.workspace.iterdir()) == []


def test_replace_failure_removes_temporary_and_reserved_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = make_paths(tmp_path)

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("injected replace failure")

    monkeypatch.setattr(restore_metadata.os, "replace", fail_replace)

    with pytest.raises(RestoreStagingError, match="installed atomically"):
        write_pending(paths, pending())

    assert not paths.pending.exists()
    assert list(paths.workspace.iterdir()) == []


def test_pending_metadata_failpoints_preserve_exact_crash_evidence(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)

    def interrupt_temporary(point: RestoreFailpoint) -> None:
        if point is RestoreFailpoint.PENDING_METADATA_TEMPORARY_WRITTEN:
            raise SimulatedInterruption

    with pytest.raises(SimulatedInterruption):
        write_pending(paths, pending(), failpoint=interrupt_temporary)

    assert not paths.pending.exists()
    temporary = list(paths.workspace.glob(f".pending.json.{REQUEST_ID}.*.tmp"))
    assert len(temporary) == 1

    temporary[0].unlink()

    def interrupt_replaced(point: RestoreFailpoint) -> None:
        if point is RestoreFailpoint.PENDING_METADATA_REPLACED:
            raise SimulatedInterruption

    with pytest.raises(SimulatedInterruption):
        write_pending(paths, pending(), failpoint=interrupt_replaced)

    assert load_pending(paths) == pending()
    assert list(paths.workspace.glob("*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows directory-fsync fallback")
def test_windows_directory_fsync_fallback_is_non_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_directory_open(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise OSError("directory handles unsupported")

    monkeypatch.setattr(restore_workspace.os, "open", reject_directory_open)

    restore_workspace.sync_directory_best_effort(tmp_path)
