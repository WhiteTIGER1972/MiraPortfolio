"""Real-service cross-layer coverage for Settings and Recovery Tools."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
)

from app.application.preferences import LogLevelPreference
from app.application.queries import ListPortfoliosQuery
from app.core import runtime_paths
from app.core.container import build_container
from app.core.exceptions import DatabaseError
from app.core.settings import Settings
from app.infrastructure.database import DatabaseManager
from app.infrastructure.diagnostics import verify_support_bundle
from app.infrastructure.persistence.database_preparation import prepare_database
from app.infrastructure.persistence.sqlite_validation import verify_sqlite_integrity
from app.infrastructure.preferences import resolve_preferences
from app.infrastructure.resilience import GlobalErrorBoundary
from app.ui.application import MiraApplication
from app.ui.dialogs.settings_recovery_dialog import SettingsRecoveryDialog
from app.ui.windows.main_window import MainWindow


@pytest.fixture(scope="session")
def qapplication() -> Iterator[QApplication]:
    """Reuse or create the process-wide offscreen Qt application."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    existing = QApplication.instance()
    if existing is None:
        boundary = GlobalErrorBoundary(
            incident_logger=lambda _incident: None,
            dialog_presenter=lambda _incident, _directory: None,
            exit_requester=lambda _code: None,
        )
        application = MiraApplication([], boundary)
    elif isinstance(existing, QApplication):
        application = existing
    else:
        raise RuntimeError("A non-GUI Qt application instance already exists.")
    yield application
    application.processEvents()


def _settings(root: Path) -> Settings:
    data_directory = root / "data"
    database_directory = data_directory / "database"
    database_path = database_directory / "portfolio.db"
    return Settings(
        app_name="Mira Settings Recovery UI Integration",
        company_name="Mira Test",
        environment="test",
        auto_backup=False,
        auto_snapshot=False,
        data_directory=data_directory,
        cache_directory=root / "cache",
        database_directory=database_directory,
        database_path=database_path,
        database_url=runtime_paths.sqlite_url_for_path(database_path),
        export_directory=data_directory / "exports",
        backup_directory=data_directory / "backups",
        log_directory=root / "logs",
    )


def _wait_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout: float = 15,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            application.processEvents()
            if predicate():
                return
        time.sleep(0.002)
    raise AssertionError("Timed out waiting for a real settings/recovery UI operation.")


def _child[WidgetT](
    dialog: SettingsRecoveryDialog,
    widget_type: type[WidgetT],
    name: str,
) -> WidgetT:
    widget = dialog.findChild(widget_type, name)
    assert widget is not None
    return widget


def _database_fingerprint(path: Path) -> tuple[int, str]:
    content = path.read_bytes()
    return len(content), hashlib.sha256(content).hexdigest()


def test_real_settings_recovery_ui_preserves_active_database_and_service_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    qapplication: QApplication,
) -> None:
    for name in (
        "MIRA_LOG_LEVEL",
        "MIRA_THEME",
        "MIRA_AUTO_BACKUP",
        "MIRA_AUTO_SNAPSHOT",
    ):
        monkeypatch.delenv(name, raising=False)

    base_settings = _settings(tmp_path / "settings-recovery-ui")
    resolution = resolve_preferences(base_settings)
    settings = resolution.settings
    assert settings.log_level == "INFO"
    for directory in (
        settings.data_directory,
        settings.cache_directory,
        settings.database_directory,
        settings.export_directory,
        settings.backup_directory,
        settings.log_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    prepare_database(settings, legacy_search_directory=tmp_path)
    manager = DatabaseManager(settings)
    manager.initialize()
    container = build_container(settings, manager, resolution.service)
    window = MainWindow(container)
    dialog: SettingsRecoveryDialog | None = None

    information: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []

    def record_information(
        _parent: object,
        title: str,
        message: str,
        *_args: object,
    ) -> QMessageBox.StandardButton:
        information.append((title, message))
        return QMessageBox.StandardButton.Ok

    def record_warning(
        _parent: object,
        title: str,
        message: str,
        *_args: object,
    ) -> QMessageBox.StandardButton:
        warnings.append((title, message))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", record_information)
    monkeypatch.setattr(QMessageBox, "warning", record_warning)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )

    preference_file = runtime_paths.preferences_file(settings.data_directory)
    support_directory = settings.export_directory / "support"
    try:
        window.show()
        dialog = SettingsRecoveryDialog(
            preferences_service=container.preferences_service,
            backup_service=container.backup_service,
            restore_service=container.restore_service,
            diagnostics_service=container.diagnostics_service,
            parent=window,
        )
        _wait_until(qapplication, lambda: not dialog.operation_in_progress)
        _wait_until(qapplication, lambda: dialog.running_worker_count == 0)

        assert dialog.parent() is window
        assert not preference_file.exists()
        assert list(settings.backup_directory.iterdir()) == []
        assert not support_directory.exists()
        assert not list(settings.data_directory.rglob("*snapshot*"))
        assert container.restore_service.get_pending_restore() is None

        combo = _child(dialog, QComboBox, "logLevelCombo")
        assert combo.currentData() == LogLevelPreference.INFO.value
        combo.setCurrentIndex(combo.findData(LogLevelPreference.WARNING.value))
        _child(dialog, QPushButton, "savePreferencesButton").click()
        assert preference_file.read_bytes() == (
            b'{"format_version":2,"preferences":{"log_level":"WARNING"}}\n'
        )
        assert settings.log_level == "INFO"
        assert container.preferences_service.get_current().preferences.log_level is (
            LogLevelPreference.WARNING
        )

        assert list(settings.backup_directory.iterdir()) == []
        assert not support_directory.exists()
        _child(dialog, QPushButton, "createBackupButton").click()
        _wait_until(qapplication, lambda: not dialog.operation_in_progress)
        _wait_until(qapplication, lambda: dialog.running_worker_count == 0)

        listing = container.backup_service.list_backups()
        assert len(listing.backups) == 1
        backup = listing.backups[0]
        assert backup.backup_kind.value == "manual"
        verified_backup = container.backup_service.verify_backup(backup.path)
        assert verified_backup == backup
        table = _child(dialog, QTableWidget, "backupTable")
        assert table.rowCount() == 1
        assert table.currentRow() == 0
        assert str(settings.backup_directory) not in table.item(0, 1).text()

        _child(dialog, QPushButton, "verifySelectedBackupButton").click()
        _wait_until(qapplication, lambda: not dialog.operation_in_progress)
        _wait_until(qapplication, lambda: dialog.running_worker_count == 0)
        assert information[-1][0] == "Backup verified"
        assert backup.filename in information[-1][1]
        assert str(settings.backup_directory) not in information[-1][1]

        active_before_stage = _database_fingerprint(settings.database_path)
        _child(dialog, QPushButton, "stageSelectedRestoreButton").click()
        _wait_until(qapplication, lambda: not dialog.operation_in_progress)
        _wait_until(qapplication, lambda: dialog.running_worker_count == 0)

        active_after_stage = _database_fingerprint(settings.database_path)
        assert active_after_stage == active_before_stage
        pending = container.restore_service.get_pending_restore()
        assert pending is not None
        assert pending.backup.filename == backup.filename
        assert _child(dialog, QLabel, "pendingRestoreStatus").text() == "Restart required"
        pending_details = _child(dialog, QLabel, "pendingRestoreDetails").text()
        assert backup.filename in pending_details
        assert str(pending.request_id) in pending_details
        assert str(settings.data_directory) not in pending_details

        _child(dialog, QPushButton, "cancelPendingRestoreButton").click()
        _wait_until(qapplication, lambda: not dialog.operation_in_progress)
        _wait_until(qapplication, lambda: dialog.running_worker_count == 0)
        assert container.restore_service.get_pending_restore() is None
        assert _database_fingerprint(settings.database_path) == active_before_stage
        assert _child(dialog, QLabel, "pendingRestoreStatus").text() == ("No restore is pending")

        assert not support_directory.exists()
        _child(dialog, QPushButton, "createSupportBundleButton").click()
        _wait_until(qapplication, lambda: not dialog.operation_in_progress)
        _wait_until(qapplication, lambda: dialog.running_worker_count == 0)

        bundles = list(support_directory.glob("*.mirasupport"))
        assert len(bundles) == 1
        verified_bundle = verify_support_bundle(settings, bundles[0])
        support_summary = _child(dialog, QLabel, "supportBundleSummary").text()
        assert verified_bundle.filename in support_summary
        assert str(verified_bundle.bundle_id) in support_summary
        assert str(support_directory) not in support_summary
        assert verified_bundle.archive_sha256 not in support_summary

        assert container.portfolio_application_service.list_portfolios(ListPortfoliosQuery()) == ()
        assert _database_fingerprint(settings.database_path) == active_before_stage
        assert len(container.backup_service.list_backups().backups) == 1
        assert not list(settings.data_directory.rglob("*snapshot*"))
        assert information
        assert warnings == []
    finally:
        if dialog is not None:
            if dialog.running_worker_count:
                dialog._wait_for_workers()
            dialog.close()
            dialog.deleteLater()
        window.close()
        window.deleteLater()
        qapplication.processEvents()
        manager.shutdown()

    verify_sqlite_integrity(settings.database_path)
    assert container.restore_service.get_pending_restore() is None
    with pytest.raises(DatabaseError, match="has not been initialized"):
        _ = manager.engine
