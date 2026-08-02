"""Real v1 startup, v2 save, restart, and environment-precedence coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from app.application import bootstrap
from app.application.preferences import (
    LogLevelPreference,
    PreferenceField,
    PreferenceLoadStatus,
    PreferencesService,
    UserPreferences,
)
from app.application.restore import RestoreApplicationResult
from app.core import runtime_paths
from app.core.container import Container
from app.core.container import build_container as build_real_container
from app.core.settings import Settings
from app.infrastructure.database import DatabaseManager
from app.infrastructure.persistence.database_preparation import (
    DatabasePreparationResult,
)
from app.infrastructure.persistence.database_preparation import (
    prepare_database as prepare_real_database,
)
from app.infrastructure.persistence.database_restore import (
    apply_pending_restore as apply_real_pending_restore,
)
from app.infrastructure.persistence.sqlite_validation import verify_sqlite_integrity
from app.infrastructure.preferences import resolve_preferences


class _Window:
    def __init__(self, container: Container) -> None:
        self.container = container
        self.show_count = 0

    def show(self) -> None:
        self.show_count += 1


def _settings(root: Path) -> Settings:
    data_directory = root / "data"
    database_directory = data_directory / "database"
    database_path = database_directory / "portfolio.db"
    return Settings(
        app_name="Mira Preference Integration",
        company_name="Mira Test",
        data_directory=data_directory,
        cache_directory=root / "cache",
        database_directory=database_directory,
        database_path=database_path,
        database_url=runtime_paths.sqlite_url_for_path(database_path),
        export_directory=data_directory / "exports",
        backup_directory=data_directory / "backups",
        log_directory=root / "logs",
    )


def test_version_one_bootstrap_saves_version_two_and_restarts_with_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in (
        "MIRA_THEME",
        "MIRA_AUTO_BACKUP",
        "MIRA_AUTO_SNAPSHOT",
        "MIRA_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    root = tmp_path / "preference-cross-layer"
    base_settings = _settings(root)
    preference_file = runtime_paths.preferences_file(base_settings.data_directory)
    preference_file.parent.mkdir(parents=True)
    version_one = (
        b'{"format_version":1,"preferences":{"auto_backup":false,'
        b'"auto_snapshot":false,"log_level":"DEBUG","theme":"dark"}}\n'
    )
    preference_file.write_bytes(version_one)
    original_modified_ns = preference_file.stat().st_mtime_ns

    restore_settings: list[Settings] = []
    preparation_settings: list[Settings] = []
    managers: list[DatabaseManager] = []
    containers: list[Container] = []
    windows: list[_Window] = []

    def apply_pending_restore(settings: Settings) -> RestoreApplicationResult:
        restore_settings.append(settings)
        return apply_real_pending_restore(settings)

    def prepare_database(settings: Settings) -> DatabasePreparationResult:
        preparation_settings.append(settings)
        return prepare_real_database(settings, legacy_search_directory=tmp_path)

    def database_manager(settings: Settings) -> DatabaseManager:
        manager = DatabaseManager(settings)
        managers.append(manager)
        return manager

    def build_container(
        settings: Settings,
        database_manager: DatabaseManager,
        preferences_service: PreferencesService,
    ) -> Container:
        container = build_real_container(
            settings,
            database_manager,
            preferences_service,
        )
        containers.append(container)
        return container

    def main_window(container: Container) -> _Window:
        window = _Window(container)
        windows.append(window)
        return window

    monkeypatch.setattr(bootstrap, "get_settings", lambda: base_settings)
    monkeypatch.setattr(bootstrap, "apply_pending_restore", apply_pending_restore)
    monkeypatch.setattr(bootstrap, "prepare_database", prepare_database)
    monkeypatch.setattr(bootstrap, "DatabaseManager", database_manager)
    monkeypatch.setattr(bootstrap, "build_container", build_container)
    monkeypatch.setattr(bootstrap, "MainWindow", main_window)

    try:
        application = bootstrap.create_application()
        assert application is not None
        assert len(managers) == 1
        assert len(containers) == 1
        assert len(windows) == 1
        manager = managers[0]
        container = containers[0]
        effective = container.settings

        assert effective is not base_settings
        assert effective.log_level == "DEBUG"
        assert effective.theme == base_settings.theme == "dark"
        assert effective.auto_backup is base_settings.auto_backup is True
        assert effective.auto_snapshot is base_settings.auto_snapshot is True
        assert restore_settings == [effective]
        assert preparation_settings == [effective]
        assert getattr(manager, "_settings") is effective
        assert container.database_manager is manager
        assert getattr(container.backup_service, "_settings") is effective
        assert getattr(container.restore_service, "_settings") is effective
        assert getattr(container.diagnostics_service, "_settings") is effective
        assert getattr(container.preferences_service, "_settings") is effective
        assert windows[0].container is container
        assert windows[0].show_count == 1
        snapshot = container.preferences_service.get_current()
        assert snapshot.status is PreferenceLoadStatus.LOADED
        assert snapshot.format_version == 1
        assert snapshot.preferences == UserPreferences(log_level=LogLevelPreference.DEBUG)
        assert snapshot.overridden_by_environment == frozenset()
        assert preference_file.read_bytes() == version_one
        assert preference_file.stat().st_mtime_ns == original_modified_ns

        saved = container.preferences_service.save(
            UserPreferences(log_level=LogLevelPreference.WARNING)
        )
        version_two = preference_file.read_bytes()
        assert version_two == (b'{"format_version":2,"preferences":{"log_level":"WARNING"}}\n')
        assert b"theme" not in version_two
        assert b"auto_backup" not in version_two
        assert b"auto_snapshot" not in version_two
        assert saved.restart_required_fields == frozenset({PreferenceField.LOG_LEVEL})
        assert effective.log_level == "DEBUG"
        assert effective.auto_backup is True
        assert effective.auto_snapshot is True

        restart = resolve_preferences(_settings(root))
        assert restart.settings.log_level == "WARNING"
        assert restart.settings.theme == "dark"
        assert restart.settings.auto_backup is True
        assert restart.settings.auto_snapshot is True
        assert restart.service.get_current().format_version == 2
        assert preference_file.read_bytes() == version_two

        monkeypatch.setenv("MIRA_LOG_LEVEL", "ERROR")
        environment_restart = resolve_preferences(_settings(root))
        assert environment_restart.settings.log_level == "ERROR"
        assert environment_restart.service.get_current().overridden_by_environment == frozenset(
            {PreferenceField.LOG_LEVEL}
        )
        assert preference_file.read_bytes() == version_two
    finally:
        for manager in managers:
            manager.shutdown()
        logger.remove()

    verify_sqlite_integrity(base_settings.database_path)
    assert list(base_settings.backup_directory.iterdir()) == []
    assert not list(root.rglob("*.mirasupport"))
    assert not list(root.rglob("*snapshot*"))
    assert not (base_settings.database_directory / "restore").exists()
