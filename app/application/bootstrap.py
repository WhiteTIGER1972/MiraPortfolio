"""Desktop application bootstrap."""

from loguru import logger
from PySide6.QtWidgets import QApplication

from app.core.container import Container
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.infrastructure.database import DatabaseManager
from app.ui.theme.manager import ThemeManager
from app.ui.windows.main_window import MainWindow


def create_application() -> QApplication:
    """Construct dependencies, initialize storage, and show the main window."""
    settings = get_settings()
    for directory in (
        settings.cache_directory,
        settings.database_directory,
        settings.export_directory,
        settings.backup_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    configure_logging(settings)
    database_manager = DatabaseManager(settings).initialize()
    if not database_manager.health_check():
        database_manager.shutdown()
        raise RuntimeError("Mira Portfolio database health check failed.")

    container = Container(
        settings=settings,
        database_manager=database_manager,
        session_factory=database_manager.session_factory,
    )
    application = QApplication.instance() or QApplication([])
    application.setApplicationName(settings.app_name)
    application.setOrganizationName(settings.company_name)
    application.aboutToQuit.connect(database_manager.shutdown)
    ThemeManager.apply(application)
    window = MainWindow(container)
    window.show()
    logger.info("{} started", settings.app_name)
    return application
