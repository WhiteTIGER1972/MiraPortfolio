"""Stable public facade for restart-safe SQLite restore staging and startup."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.application.backup import BackupService
from app.application.restore import (
    RestoreApplicationResult,
    RestoreService,
    RestoreStageResult,
)
from app.core.exceptions import RestoreApplicationError
from app.core.settings import Settings
from app.infrastructure.persistence.database_backup import SQLiteBackupService
from app.infrastructure.persistence.restore_failpoints import (
    RestoreFailpointCallback,
)
from app.infrastructure.persistence.restore_metadata import (
    UNVERSIONED_REVISION,
    require_utc,
)
from app.infrastructure.persistence.restore_staging import RestoreStagingManager
from app.infrastructure.persistence.restore_transaction import (
    RestoreTransactionManager,
    no_pending_result,
)
from app.infrastructure.persistence.restore_workspace import (
    OPERATION_FILENAME,
    PENDING_FILENAME,
    RESTORE_DIRECTORY_NAME,
    optional_startup_restore_paths,
    path_present,
    require_restore_workspace,
)


class SQLiteRestoreService(RestoreService):
    """Stage verified restore requests without touching the active database."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
        script_location: Path | None = None,
        _failpoint: RestoreFailpointCallback | None = None,
    ) -> None:
        self._settings = settings
        self._manager = RestoreStagingManager(
            settings,
            clock=clock,
            uuid_factory=uuid_factory,
            script_location=script_location,
            failpoint=_failpoint,
        )

    def stage_restore(self, backup_path: Path) -> RestoreStageResult:
        """Stage one independently owned database for startup installation."""
        return self._manager.stage_restore(backup_path)

    def get_pending_restore(self) -> RestoreStageResult | None:
        """Return a fully revalidated pending restore request."""
        return self._manager.get_pending_restore()

    def cancel_pending_restore(self) -> bool:
        """Cancel only the exact validated pending restore request."""
        return self._manager.cancel_pending_restore()


class StartupRestoreCoordinator:
    """Apply or recover pending restore state before any runtime engine exists."""

    def __init__(
        self,
        settings: Settings,
        *,
        backup_service: BackupService | None = None,
        clock: Callable[[], datetime] | None = None,
        script_location: Path | None = None,
        _failpoint: RestoreFailpointCallback | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or _utc_now
        self._manager = RestoreTransactionManager(
            settings,
            backup_service or SQLiteBackupService(settings),
            clock=self._clock,
            script_location=script_location,
            failpoint=_failpoint,
        )

    def apply_pending_restore(self) -> RestoreApplicationResult:
        """Recover an interrupted operation or atomically apply one pending restore."""
        now = require_utc(self._clock(), RestoreApplicationError)
        paths = optional_startup_restore_paths(self._settings)
        if paths is None or not path_present(paths.workspace):
            return no_pending_result(now)
        require_restore_workspace(paths.workspace)
        return self._manager.apply_or_recover(paths, now)


def apply_pending_restore(settings: Settings) -> RestoreApplicationResult:
    """Apply pending startup restore state without constructing a Container."""
    return StartupRestoreCoordinator(settings).apply_pending_restore()


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "OPERATION_FILENAME",
    "PENDING_FILENAME",
    "RESTORE_DIRECTORY_NAME",
    "SQLiteRestoreService",
    "StartupRestoreCoordinator",
    "UNVERSIONED_REVISION",
    "apply_pending_restore",
]
