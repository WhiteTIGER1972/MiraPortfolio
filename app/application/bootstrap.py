"""Desktop application bootstrap."""

import threading
from collections.abc import Callable

from loguru import logger
from PySide6.QtWidgets import QApplication

from app.application.resilience import IncidentPhase
from app.core.container import build_container
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.infrastructure.database import DatabaseManager
from app.infrastructure.persistence.database_preparation import prepare_database
from app.infrastructure.persistence.database_restore import apply_pending_restore
from app.infrastructure.resilience import GlobalErrorBoundary
from app.ui.application import MiraApplication
from app.ui.theme.manager import ThemeManager
from app.ui.windows.main_window import MainWindow


def create_application() -> QApplication:
    """Construct the desktop application while preserving the public bootstrap API."""
    return _create_application(None)


def create_application_with_boundary(boundary: GlobalErrorBoundary) -> QApplication:
    """Construct the desktop application with explicit global phase tracking."""
    return _create_application(boundary)


def _create_application(boundary: GlobalErrorBoundary | None) -> QApplication:
    """Construct dependencies and transfer lifecycle ownership after window display."""
    _enter_phase(boundary, IncidentPhase.STARTUP_SETTINGS)
    settings = get_settings()

    _enter_phase(boundary, IncidentPhase.STARTUP_DIRECTORIES)
    for directory in (
        settings.data_directory,
        settings.cache_directory,
        settings.database_directory,
        settings.export_directory,
        settings.backup_directory,
        settings.log_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    _enter_phase(boundary, IncidentPhase.STARTUP_LOGGING)
    configure_logging(settings)
    if boundary is not None:
        boundary.mark_logging_available(settings.log_directory)

    database_manager: DatabaseManager | None = None
    shutdown_once: _ShutdownOnce | None = None
    lifecycle_transferred = False
    try:
        _enter_phase(boundary, IncidentPhase.STARTUP_RESTORE)
        restore = apply_pending_restore(settings)
        logger.info("Database restore startup outcome: {}", restore.outcome.value)
        if restore.pre_restore_backup is not None:
            logger.info(
                "Pre-restore safety backup retained: {}",
                restore.pre_restore_backup.filename,
            )

        _enter_phase(boundary, IncidentPhase.STARTUP_DATABASE_PREPARATION)
        preparation = prepare_database(settings)
        logger.info("Database preparation completed: {}", preparation.outcome.value)

        _enter_phase(boundary, IncidentPhase.STARTUP_DATABASE_INITIALIZATION)
        database_manager = DatabaseManager(settings)
        shutdown_once = _ShutdownOnce(database_manager.shutdown)
        database_manager.initialize()
        if not database_manager.health_check():
            raise RuntimeError("Mira Portfolio database health check failed.")

        _enter_phase(boundary, IncidentPhase.STARTUP_CONTAINER)
        container = build_container(
            settings=settings,
            database_manager=database_manager,
        )

        _enter_phase(boundary, IncidentPhase.STARTUP_QT)
        existing_application = QApplication.instance()
        if existing_application is None:
            application = QApplication([]) if boundary is None else MiraApplication([], boundary)
        elif isinstance(existing_application, QApplication):
            application = existing_application
        else:
            raise RuntimeError("A non-GUI Qt application instance already exists.")
        if boundary is not None:
            if isinstance(application, MiraApplication):
                application.bind_error_boundary(boundary)
            boundary.bind_qt_application(application)

        application.setApplicationName(settings.app_name)
        application.setOrganizationName(settings.company_name)
        application.aboutToQuit.connect(shutdown_once)

        _enter_phase(boundary, IncidentPhase.STARTUP_THEME)
        ThemeManager.apply(application)

        _enter_phase(boundary, IncidentPhase.STARTUP_UI)
        window = MainWindow(container)
        _enter_phase(boundary, IncidentPhase.STARTUP_UI)
        window.show()
        logger.info("Mira Portfolio started")
        lifecycle_transferred = True
        return application
    except BaseException:
        if shutdown_once is not None and not lifecycle_transferred:
            shutdown_once()
        raise


class _ShutdownOnce:
    """Ensure bootstrap and Qt shutdown paths cannot dispose twice."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._lock = threading.Lock()
        self._called = False

    def __call__(self) -> None:
        with self._lock:
            if self._called:
                return
            self._called = True
        self._callback()


def _enter_phase(
    boundary: GlobalErrorBoundary | None,
    phase: IncidentPhase,
) -> None:
    if boundary is not None:
        boundary.enter_phase(phase)


__all__ = ["create_application", "create_application_with_boundary"]
