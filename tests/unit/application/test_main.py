"""Tests for guarded desktop startup and controlled exit codes."""

from __future__ import annotations

import importlib
import io
from pathlib import Path

import pytest

from app.application import bootstrap
from app.application.resilience import ErrorIncident, IncidentPhase
from app.core.settings import Settings
from app.infrastructure.resilience import GlobalErrorBoundary

entrypoint = importlib.import_module("app.__main__")


def temporary_settings(tmp_path: Path) -> Settings:
    root = tmp_path / "runtime"
    return Settings(
        _env_file=None,
        data_directory=root / "data",
        cache_directory=root / "cache",
        database_directory=root / "data" / "database",
        export_directory=root / "data" / "exports",
        backup_directory=root / "data" / "backups",
        log_directory=root / "logs",
        database_path=root / "data" / "database" / "portfolio.db",
        database_url=f"sqlite:///{(root / 'data' / 'database' / 'portfolio.db').as_posix()}",
    )


def guarded_boundary(
    incidents: list[ErrorIncident],
    stderr: io.StringIO | None = None,
) -> GlobalErrorBoundary:
    return GlobalErrorBoundary(
        incident_logger=incidents.append,
        dialog_presenter=lambda _incident, _directory: None,
        exit_requester=lambda _code: None,
        stderr=stderr,
    )


def test_settings_failure_returns_one_and_records_settings_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incidents: list[ErrorIncident] = []

    def fail_settings() -> Settings:
        raise RuntimeError("private settings value")

    monkeypatch.setattr(bootstrap, "get_settings", fail_settings)

    result = entrypoint.run_desktop_application(guarded_boundary(incidents))

    assert result == 1
    assert len(incidents) == 1
    assert incidents[0].phase is IncidentPhase.STARTUP_SETTINGS
    assert "private settings value" not in repr(incidents[0])


def test_directory_creation_failure_returns_one_before_logging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    incidents: list[ErrorIncident] = []
    settings = temporary_settings(tmp_path)
    settings.data_directory.parent.mkdir(parents=True)
    settings.data_directory.write_text("not a directory", encoding="utf-8")
    logging_calls: list[Settings] = []
    monkeypatch.setattr(bootstrap, "get_settings", lambda: settings)
    monkeypatch.setattr(bootstrap, "configure_logging", logging_calls.append)

    result = entrypoint.run_desktop_application(guarded_boundary(incidents))

    assert result == 1
    assert incidents[0].phase is IncidentPhase.STARTUP_DIRECTORIES
    assert logging_calls == []


def test_logging_failure_uses_only_bounded_sanitized_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = temporary_settings(tmp_path)
    stderr = io.StringIO()
    dialogs: list[ErrorIncident] = []

    def fail_logging(_: Settings) -> None:
        raise RuntimeError(
            rf"password=hunter2 at {tmp_path}\private.log using {settings.database_url}"
        )

    monkeypatch.setattr(bootstrap, "get_settings", lambda: settings)
    monkeypatch.setattr(bootstrap, "configure_logging", fail_logging)
    boundary = GlobalErrorBoundary(
        dialog_presenter=lambda incident, _directory: dialogs.append(incident),
        exit_requester=lambda _code: None,
        stderr=stderr,
    )

    result = entrypoint.run_desktop_application(boundary)
    output = stderr.getvalue()

    assert result == 1
    assert len(dialogs) == 1
    assert dialogs[0].phase is IncidentPhase.STARTUP_LOGGING
    assert len(output) <= 512
    assert "hunter2" not in output
    assert str(tmp_path) not in output
    assert settings.database_url not in output
    assert not (settings.log_directory / "mira-portfolio.log").exists()


def test_restore_failure_returns_one_and_stops_all_later_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = temporary_settings(tmp_path)
    incidents: list[ErrorIncident] = []
    later_calls: list[str] = []
    monkeypatch.setattr(bootstrap, "get_settings", lambda: settings)
    monkeypatch.setattr(bootstrap, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(
        bootstrap,
        "apply_pending_restore",
        lambda _settings: (_ for _ in ()).throw(RuntimeError("private restore value")),
    )
    monkeypatch.setattr(
        bootstrap,
        "prepare_database",
        lambda _settings: later_calls.append("prepare"),
    )
    monkeypatch.setattr(
        bootstrap,
        "DatabaseManager",
        lambda _settings: later_calls.append("manager"),
    )
    monkeypatch.setattr(
        bootstrap,
        "build_container",
        lambda **_kwargs: later_calls.append("container"),
    )
    monkeypatch.setattr(
        bootstrap,
        "MainWindow",
        lambda _container: later_calls.append("window"),
    )

    result = entrypoint.run_desktop_application(guarded_boundary(incidents))

    assert result == 1
    assert incidents[0].phase is IncidentPhase.STARTUP_RESTORE
    assert later_calls == []


def test_event_loop_exit_code_is_returned_and_hooks_are_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeApplication:
        def exec(self) -> int:
            return 7

    incidents: list[ErrorIncident] = []
    boundary = guarded_boundary(incidents)
    original_sys_hook = __import__("sys").excepthook
    monkeypatch.setattr(
        entrypoint,
        "create_application_with_boundary",
        lambda _boundary: FakeApplication(),
    )

    result = entrypoint.run_desktop_application(boundary)

    assert result == 7
    assert incidents == []
    assert __import__("sys").excepthook is original_sys_hook


def test_exception_raised_directly_by_event_loop_returns_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingApplication:
        def exec(self) -> int:
            raise RuntimeError("private exec value")

    incidents: list[ErrorIncident] = []
    monkeypatch.setattr(
        entrypoint,
        "create_application_with_boundary",
        lambda _boundary: FailingApplication(),
    )

    result = entrypoint.run_desktop_application(guarded_boundary(incidents))

    assert result == 1
    assert len(incidents) == 1
    assert incidents[0].phase is IncidentPhase.RUNTIME_MAIN_THREAD
