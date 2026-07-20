"""Tests for desktop bootstrap lifecycle delegation."""

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Self, get_type_hints

import pytest

from app.application import bootstrap
from app.core.container import Container
from app.core.settings import Settings
from app.ui.theme.manager import ThemeManager
from app.ui.windows.main_window import MainWindow


class FakeSignal:
    """Record signal callbacks without starting Qt."""

    def __init__(self) -> None:
        self.callbacks: list[Callable[[], None]] = []

    def connect(self, callback: Callable[[], None]) -> None:
        self.callbacks.append(callback)


class FakeApplication:
    """Provide the QApplication surface used by create_application."""

    existing: ClassVar[object | None] = None
    created_arguments: ClassVar[list[list[str]]] = []

    def __init__(self, arguments: list[str]) -> None:
        type(self).existing = self
        type(self).created_arguments.append(arguments)
        self.application_name: str | None = None
        self.organization_name: str | None = None
        self.aboutToQuit = FakeSignal()

    @classmethod
    def instance(cls) -> object | None:
        return cls.existing

    def setApplicationName(self, name: str) -> None:  # noqa: N802
        self.application_name = name

    def setOrganizationName(self, name: str) -> None:  # noqa: N802
        self.organization_name = name


class FakeDatabaseManager:
    """Record database lifecycle calls made by bootstrap."""

    def __init__(
        self,
        settings: Settings,
        events: list[str],
        *,
        healthy: bool = True,
    ) -> None:
        self.settings = settings
        self.events = events
        self.healthy = healthy
        self.initialize_count = 0
        self.health_check_count = 0
        self.shutdown_count = 0

    def initialize(self) -> Self:
        self.initialize_count += 1
        self.events.append("database.initialize")
        return self

    def health_check(self) -> bool:
        self.health_check_count += 1
        self.events.append("database.health_check")
        return self.healthy

    def shutdown(self) -> None:
        self.shutdown_count += 1
        self.events.append("database.shutdown")


class FakeWindow:
    """Record construction and visibility without creating widgets."""

    def __init__(self, container: object, events: list[str]) -> None:
        self.container = container
        self.events = events
        self.show_count = 0
        events.append("window.construct")

    def show(self) -> None:
        self.show_count += 1
        self.events.append("window.show")


class BootstrapHarness:
    """Install narrow bootstrap fakes and expose their observations."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        healthy: bool = True,
        build_error: Exception | None = None,
    ) -> None:
        root = tmp_path / "bootstrap"
        self.settings = Settings(
            app_name="Mira Test",
            company_name="Mira Company",
            database_url=f"sqlite:///{(root / 'unused.db').as_posix()}",
            cache_directory=root / "cache",
            database_directory=root / "data",
            export_directory=root / "exports",
            backup_directory=root / "backups",
        )
        self.events: list[str] = []
        self.manager = FakeDatabaseManager(
            self.settings,
            self.events,
            healthy=healthy,
        )
        self.container = object()
        self.build_error = build_error
        self.database_factory_calls: list[Settings] = []
        self.build_calls: list[tuple[Settings, FakeDatabaseManager]] = []
        self.theme_calls: list[FakeApplication] = []
        self.windows: list[FakeWindow] = []

        FakeApplication.existing = None
        FakeApplication.created_arguments = []
        monkeypatch.setattr(bootstrap, "get_settings", lambda: self.settings)
        monkeypatch.setattr(bootstrap, "configure_logging", self.configure_logging)
        monkeypatch.setattr(bootstrap, "DatabaseManager", self.database_manager_factory)
        monkeypatch.setattr(bootstrap, "build_container", self.build_container)
        monkeypatch.setattr(bootstrap, "QApplication", FakeApplication)
        monkeypatch.setattr(ThemeManager, "apply", self.apply_theme)
        monkeypatch.setattr(bootstrap, "MainWindow", self.create_window)

    def configure_logging(self, settings: Settings) -> None:
        assert settings is self.settings
        self.events.append("logging.configure")

    def database_manager_factory(self, settings: Settings) -> FakeDatabaseManager:
        self.database_factory_calls.append(settings)
        self.events.append("database.construct")
        return self.manager

    def build_container(
        self,
        settings: Settings,
        database_manager: FakeDatabaseManager,
    ) -> object:
        self.build_calls.append((settings, database_manager))
        self.events.append("container.build")
        if self.build_error is not None:
            raise self.build_error
        return self.container

    def apply_theme(self, application: FakeApplication) -> None:
        self.theme_calls.append(application)
        self.events.append("theme.apply")

    def create_window(self, container: object) -> FakeWindow:
        window = FakeWindow(container, self.events)
        self.windows.append(window)
        return window


def test_successful_bootstrap_delegates_composition_and_window_injection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = BootstrapHarness(monkeypatch, tmp_path)

    application = bootstrap.create_application()

    assert isinstance(application, FakeApplication)
    assert harness.database_factory_calls == [harness.settings]
    assert harness.manager.initialize_count == 1
    assert harness.manager.health_check_count == 1
    assert harness.build_calls == [(harness.settings, harness.manager)]
    assert len(harness.windows) == 1
    assert harness.windows[0].container is harness.container
    assert harness.windows[0].show_count == 1
    assert harness.theme_calls == [application]
    assert application.application_name == harness.settings.app_name
    assert application.organization_name == harness.settings.company_name
    assert FakeApplication.created_arguments == [[]]
    assert harness.events.index("logging.configure") < harness.events.index("database.initialize")
    assert harness.events.index("database.health_check") < harness.events.index("container.build")
    assert harness.events.index("container.build") < harness.events.index("theme.apply")


def test_bootstrap_preserves_directory_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = BootstrapHarness(monkeypatch, tmp_path)

    bootstrap.create_application()

    for directory in (
        harness.settings.cache_directory,
        harness.settings.database_directory,
        harness.settings.export_directory,
        harness.settings.backup_directory,
    ):
        assert directory.is_dir()


def test_health_check_failure_stops_before_composition_or_ui(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = BootstrapHarness(monkeypatch, tmp_path, healthy=False)

    with pytest.raises(RuntimeError, match="database health check failed"):
        bootstrap.create_application()

    assert harness.manager.initialize_count == 1
    assert harness.manager.health_check_count == 1
    assert harness.manager.shutdown_count == 1
    assert harness.build_calls == []
    assert harness.theme_calls == []
    assert harness.windows == []
    assert FakeApplication.created_arguments == []


def test_container_build_failure_propagates_before_ui_without_new_translation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    failure = RuntimeError("composition failed")
    harness = BootstrapHarness(monkeypatch, tmp_path, build_error=failure)

    with pytest.raises(RuntimeError) as raised:
        bootstrap.create_application()

    assert raised.value is failure
    assert harness.build_calls == [(harness.settings, harness.manager)]
    assert harness.manager.shutdown_count == 0
    assert harness.theme_calls == []
    assert harness.windows == []
    assert FakeApplication.created_arguments == []


def test_existing_qapplication_is_reused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = BootstrapHarness(monkeypatch, tmp_path)
    existing = FakeApplication(["existing"])
    FakeApplication.created_arguments.clear()

    application = bootstrap.create_application()

    assert application is existing
    assert FakeApplication.created_arguments == []
    assert harness.theme_calls == [existing]
    assert harness.windows[0].container is harness.container


def test_non_gui_qt_instance_fails_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = BootstrapHarness(monkeypatch, tmp_path)
    FakeApplication.existing = object()

    with pytest.raises(RuntimeError, match="non-GUI Qt application"):
        bootstrap.create_application()

    assert harness.build_calls == [(harness.settings, harness.manager)]
    assert harness.theme_calls == []
    assert harness.windows == []
    assert FakeApplication.created_arguments == []


def test_shutdown_signal_is_connected_to_initialized_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = BootstrapHarness(monkeypatch, tmp_path)

    application = bootstrap.create_application()

    assert isinstance(application, FakeApplication)
    assert len(application.aboutToQuit.callbacks) == 1
    application.aboutToQuit.callbacks[0]()
    assert harness.manager.shutdown_count == 1


def test_bootstrap_source_delegates_graph_construction_without_service_behavior() -> None:
    source = Path(bootstrap.__file__).read_text(encoding="utf-8")

    assert "from app.core.container import build_container" in source
    assert "build_container(" in source
    assert "MainWindow(container)" in source
    assert "Container(" not in source
    for forbidden_text in (
        "DefaultPortfolioApplicationService",
        "DefaultAssetApplicationService",
        "DefaultMarketPriceApplicationService",
        "DefaultPortfolioDashboardQueryService",
        "SQLAlchemyUnitOfWork",
        "Repository",
        ".create_asset(",
        ".list_assets(",
        ".create_portfolio(",
        ".buy_asset(",
        ".sell_asset(",
        ".record_market_price(",
        ".get_latest_market_price(",
        ".get_dashboard(",
        "Session(",
        "select(",
        "text(",
    ):
        assert forbidden_text not in source


def test_main_window_contract_remains_container_only() -> None:
    parameters = list(inspect.signature(MainWindow.__init__).parameters)

    assert parameters == ["self", "container"]
    assert get_type_hints(MainWindow.__init__)["container"] is Container
