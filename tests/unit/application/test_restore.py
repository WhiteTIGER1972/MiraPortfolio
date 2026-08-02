"""Tests for the application-facing restart-safe restore contract."""

import inspect
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import get_type_hints
from uuid import UUID

import pytest

from app.application.backup import BackupKind
from app.application.restore import (
    RestoreApplicationResult,
    RestoreBackupIdentity,
    RestoreOutcome,
    RestoreService,
    RestoreStageResult,
)


def test_restore_stage_result_and_backup_identity_are_immutable() -> None:
    identity = RestoreBackupIdentity(
        filename="source.mirabackup",
        app_version="0.1.0",
        alembic_revision="20260718_0001",
        database_size=123,
        database_sha256="a" * 64,
        backup_kind=BackupKind.MANUAL,
    )
    result = RestoreStageResult(
        request_id=UUID(int=1),
        backup=identity,
        staged_at=datetime(2026, 7, 30, tzinfo=UTC),
        restart_required=True,
        outcome=RestoreOutcome.STAGED,
    )

    assert tuple(field.name for field in fields(RestoreStageResult)) == (
        "request_id",
        "backup",
        "staged_at",
        "restart_required",
        "outcome",
    )
    assert "__dict__" not in RestoreStageResult.__slots__
    assert "__dict__" not in RestoreBackupIdentity.__slots__
    with pytest.raises(FrozenInstanceError):
        result.restart_required = False


def test_restore_application_result_is_exact_immutable_status_dto() -> None:
    result = RestoreApplicationResult(
        request_id=None,
        outcome=RestoreOutcome.NO_PENDING_RESTORE,
        restored_backup=None,
        pre_restore_backup=None,
        applied_at=datetime(2026, 7, 30, tzinfo=UTC),
    )

    assert tuple(field.name for field in fields(RestoreApplicationResult)) == (
        "request_id",
        "outcome",
        "restored_backup",
        "pre_restore_backup",
        "applied_at",
    )
    assert "__dict__" not in RestoreApplicationResult.__slots__
    with pytest.raises(FrozenInstanceError):
        result.outcome = RestoreOutcome.APPLIED


def test_restore_service_exposes_only_restart_safe_running_process_operations() -> None:
    assert RestoreService.__abstractmethods__ == {
        "stage_restore",
        "get_pending_restore",
        "cancel_pending_restore",
    }
    assert list(inspect.signature(RestoreService.stage_restore).parameters) == [
        "self",
        "backup_path",
    ]
    assert get_type_hints(RestoreService.stage_restore)["backup_path"] is Path
    assert not hasattr(RestoreService, "restore_now")
    assert not hasattr(RestoreService, "apply_pending_restore")


def test_restore_contract_has_no_infrastructure_ui_or_database_types() -> None:
    source = Path(inspect.getsourcefile(RestoreService) or "").read_text(encoding="utf-8")

    for forbidden in (
        "app.infrastructure",
        "app.ui",
        "app.domain",
        "sqlite3",
        "sqlalchemy",
        "database_url",
        "Engine",
        "Session",
    ):
        assert forbidden not in source
