"""Tests for the minimal privacy-safe fatal error dialog."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from app.application.resilience import (
    ErrorIncident,
    IncidentPhase,
    IncidentSeverity,
)
from app.ui.dialogs.fatal_error_dialog import FatalErrorDialog


def incident() -> ErrorIncident:
    return ErrorIncident(
        incident_id=UUID("12345678-1234-4234-8234-123456789abc"),
        occurred_at_utc=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
        phase=IncidentPhase.STARTUP_DATABASE_INITIALIZATION,
        severity=IncidentSeverity.FATAL,
        exception_type="RuntimeError",
        safe_summary="The database could not be initialized.",
        safe_frames=(),
    )


def visible_text(dialog: FatalErrorDialog) -> str:
    return "\n".join(label.text() for label in dialog.findChildren(QLabel))


def button(dialog: FatalErrorDialog, object_name: str) -> QPushButton:
    result = dialog.findChild(QPushButton, object_name)
    assert result is not None
    return result


def test_dialog_contains_safe_reference_phase_timestamp_and_guidance(
    qapplication: QApplication,
    tmp_path: Path,
) -> None:
    log_directory = tmp_path / "private" / "logs"
    dialog = FatalErrorDialog(incident(), log_directory)
    text = visible_text(dialog)

    assert "Mira Portfolio" in text
    assert "unexpected error" in text
    assert "must close to protect your data" in text
    assert "12345678-1234-4234-8234-123456789abc" in text
    assert "Database initialization" in text
    assert "2026-08-02T09:30:00+00:00" in text
    assert "logs" in text.casefold()


def test_dialog_and_clipboard_text_exclude_raw_error_and_runtime_paths(
    qapplication: QApplication,
    tmp_path: Path,
) -> None:
    log_directory = tmp_path / "private" / "alice" / "logs"
    dialog = FatalErrorDialog(incident(), log_directory)
    rendered = visible_text(dialog) + dialog.safe_clipboard_text()

    assert str(log_directory) not in rendered
    assert "postgresql://" not in rendered
    assert "password=" not in rendered
    assert "RuntimeError" not in rendered


def test_copy_action_places_only_sanitized_text_on_clipboard(
    qapplication: QApplication,
    tmp_path: Path,
) -> None:
    dialog = FatalErrorDialog(incident(), tmp_path / "private" / "logs")

    button(dialog, "copyIncidentReferenceButton").click()
    qapplication.processEvents()

    clipboard = QApplication.clipboard().text()
    assert clipboard == dialog.safe_clipboard_text()
    assert str(tmp_path) not in clipboard
    assert "12345678-1234-4234-8234-123456789abc" in clipboard


def test_clipboard_failure_is_contained_without_exposing_details(
    qapplication: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_clipboard() -> object:
        raise RuntimeError("private clipboard value")

    monkeypatch.setattr(QApplication, "clipboard", fail_clipboard)
    dialog = FatalErrorDialog(incident(), tmp_path / "logs")

    button(dialog, "copyIncidentReferenceButton").click()
    qapplication.processEvents()

    status = dialog.findChild(QLabel, "fatalErrorStatus")
    assert status is not None
    assert status.text() == "The incident reference could not be copied."
    assert "private clipboard value" not in visible_text(dialog)


def test_log_directory_opens_only_after_explicit_action(
    qapplication: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[QUrl] = []

    def record(url: QUrl) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", record)
    log_directory = tmp_path / "private" / "logs"
    dialog = FatalErrorDialog(incident(), log_directory)

    assert opened == []
    button(dialog, "openLogDirectoryButton").click()
    qapplication.processEvents()

    assert len(opened) == 1
    assert Path(opened[0].toLocalFile()) == log_directory


def test_log_open_failure_is_contained_and_creates_no_support_bundle(
    qapplication: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: False)
    dialog = FatalErrorDialog(incident(), tmp_path / "logs")

    button(dialog, "openLogDirectoryButton").click()
    qapplication.processEvents()

    status = dialog.findChild(QLabel, "fatalErrorStatus")
    assert status is not None
    assert status.text() == "The log folder could not be opened."
    assert list(tmp_path.rglob("*.mirasupport")) == []
