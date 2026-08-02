"""Cross-layer startup and Qt runtime fatal-boundary scenarios."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Self

import pytest
from loguru import logger

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.application import bootstrap  # noqa: E402
from app.application.resilience import ErrorIncident  # noqa: E402
from app.core import config  # noqa: E402
from app.core.settings import Settings  # noqa: E402
from app.infrastructure.database import DatabaseManager  # noqa: E402
from app.infrastructure.persistence.sqlite_validation import (  # noqa: E402
    validate_current_sqlite_database,
)
from app.infrastructure.resilience import GlobalErrorBoundary  # noqa: E402

entrypoint = importlib.import_module("app.__main__")


class TrackingDatabaseManager(DatabaseManager):
    """Use the real manager while exposing lifecycle observations."""

    instances: list[TrackingDatabaseManager] = []

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.shutdown_count = 0
        type(self).instances.append(self)

    def initialize(self) -> Self:
        super().initialize()
        return self

    def shutdown(self) -> None:
        self.shutdown_count += 1
        super().shutdown()


def resilience_settings(tmp_path: Path) -> Settings:
    root = tmp_path / "private-runtime"
    database_path = root / "data" / "database" / "portfolio.db"
    return Settings(
        _env_file=None,
        app_name="Mira Portfolio Test",
        data_directory=root / "data",
        cache_directory=root / "cache",
        database_directory=database_path.parent,
        export_directory=root / "data" / "exports",
        backup_directory=root / "data" / "backups",
        log_directory=root / "logs",
        database_path=database_path,
        database_url=f"sqlite:///{database_path.as_posix()}",
    )


def read_incident_log(settings: Settings) -> str:
    return (settings.log_directory / config.LOG_FILENAME).read_text(encoding="utf-8")


def assert_private_inputs_absent(
    content: str,
    settings: Settings,
    sensitive_message: str,
) -> None:
    assert sensitive_message not in content
    assert str(settings.data_directory) not in content
    assert str(settings.database_path) not in content
    assert settings.database_url not in content


def test_cross_layer_startup_failure_is_sanitized_and_releases_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = resilience_settings(tmp_path)
    sensitive_message = (
        rf"password=hunter2 at {settings.database_path} using {settings.database_url}"
    )
    dialogs: list[ErrorIncident] = []
    exits: list[int] = []
    window_calls: list[object] = []
    TrackingDatabaseManager.instances = []
    monkeypatch.setattr(bootstrap, "get_settings", lambda: settings)
    monkeypatch.setattr(bootstrap, "DatabaseManager", TrackingDatabaseManager)

    def fail_container(**_: object) -> object:
        raise RuntimeError(sensitive_message)

    monkeypatch.setattr(bootstrap, "build_container", fail_container)
    monkeypatch.setattr(bootstrap, "MainWindow", window_calls.append)
    boundary = GlobalErrorBoundary(
        dialog_presenter=lambda incident, _directory: dialogs.append(incident),
        exit_requester=exits.append,
    )

    try:
        result = entrypoint.run_desktop_application(boundary)
        content = read_incident_log(settings)
    finally:
        logger.remove()

    assert result == 1
    assert len(TrackingDatabaseManager.instances) == 1
    assert TrackingDatabaseManager.instances[0].shutdown_count == 1
    assert window_calls == []
    assert len(dialogs) == 1
    assert exits == [1]
    assert content.count("error_incident event=fatal_unhandled_error") == 1
    assert str(dialogs[0].incident_id) in content
    assert_private_inputs_absent(content, settings, sensitive_message)
    assert list(settings.data_directory.rglob("*.mirasupport")) == []
    validate_current_sqlite_database(settings.database_path)


class FailingEventReceiver(QObject):
    """Raise one controlled Python exception from Qt event dispatch."""

    def __init__(self, sensitive_message: str) -> None:
        super().__init__()
        self._sensitive_message = sensitive_message
        self.event_count = 0

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.User:
            self.event_count += 1
            raise RuntimeError(self._sensitive_message)
        return super().event(event)


def test_cross_layer_qt_runtime_failure_exits_once_and_releases_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = resilience_settings(tmp_path)
    sensitive_message = (
        rf"token=runtime-secret at {settings.database_path} using {settings.database_url}"
    )
    dialogs: list[ErrorIncident] = []
    receivers: list[FailingEventReceiver] = []
    TrackingDatabaseManager.instances = []
    monkeypatch.setattr(bootstrap, "get_settings", lambda: settings)
    monkeypatch.setattr(bootstrap, "DatabaseManager", TrackingDatabaseManager)
    create_application = entrypoint.create_application_with_boundary

    def create_and_schedule(boundary: GlobalErrorBoundary) -> QApplication:
        application = create_application(boundary)
        receiver = FailingEventReceiver(sensitive_message)
        receivers.append(receiver)
        QTimer.singleShot(
            0,
            lambda: QApplication.postEvent(receiver, QEvent(QEvent.Type.User)),
        )
        return application

    monkeypatch.setattr(
        entrypoint,
        "create_application_with_boundary",
        create_and_schedule,
    )
    boundary = GlobalErrorBoundary(
        dialog_presenter=lambda incident, _directory: dialogs.append(incident),
        exit_requester=QCoreApplication.exit,
    )

    try:
        result = entrypoint.run_desktop_application(boundary)
        application = QApplication.instance()
        assert isinstance(application, QApplication)
        application.closeAllWindows()
        application.processEvents()
        content = read_incident_log(settings)
    finally:
        logger.remove()

    assert result == 1
    assert len(receivers) == 1
    assert receivers[0].event_count == 1
    assert len(dialogs) == 1
    assert len(TrackingDatabaseManager.instances) == 1
    assert TrackingDatabaseManager.instances[0].shutdown_count == 1
    assert content.count("error_incident event=fatal_unhandled_error") == 1
    assert str(dialogs[0].incident_id) in content
    assert_private_inputs_absent(content, settings, sensitive_message)
    assert list(settings.data_directory.rglob("*.mirasupport")) == []
    validate_current_sqlite_database(settings.database_path)
