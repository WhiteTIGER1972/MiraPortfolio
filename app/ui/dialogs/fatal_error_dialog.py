"""Minimal privacy-safe dialog shown after a fatal application incident."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.application.resilience import ErrorIncident
from app.core import config

_PHASE_LABELS = {
    "startup_settings": "Settings startup",
    "startup_directories": "Directory startup",
    "startup_logging": "Logging startup",
    "startup_restore": "Database recovery",
    "startup_database_preparation": "Database preparation",
    "startup_database_initialization": "Database initialization",
    "startup_container": "Service initialization",
    "startup_qt": "Desktop initialization",
    "startup_theme": "Theme initialization",
    "startup_ui": "Main window startup",
    "runtime_qt_event": "Desktop event handling",
    "runtime_main_thread": "Application runtime",
    "runtime_background_thread": "Background runtime",
    "runtime_asyncio": "Asynchronous runtime",
    "runtime_unraisable": "Interpreter cleanup",
}


class FatalErrorDialog(QDialog):
    """Show only safe incident metadata and explicit recovery-adjacent actions."""

    def __init__(
        self,
        incident: ErrorIncident,
        log_directory: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._incident = incident
        self._log_directory = log_directory
        self.setWindowTitle(f"{config.APP_NAME} - Unexpected Error")
        self.setModal(True)
        self.setMinimumWidth(480)

        title = QLabel(f"{config.APP_NAME} encountered an unexpected error.")
        title.setObjectName("fatalErrorTitle")
        title.setWordWrap(True)
        explanation = QLabel(
            "The application must close to protect your data. "
            "Application logs can be used by support to investigate."
        )
        explanation.setObjectName("fatalErrorExplanation")
        explanation.setWordWrap(True)
        reference = QLabel(f"Incident reference: {incident.incident_id}")
        reference.setObjectName("fatalErrorIncidentReference")
        phase = QLabel(f"Phase: {_PHASE_LABELS[incident.phase.value]}")
        phase.setObjectName("fatalErrorPhase")
        timestamp = QLabel(f"UTC time: {incident.occurred_at_utc.isoformat()}")
        timestamp.setObjectName("fatalErrorTimestamp")
        self._status = QLabel("")
        self._status.setObjectName("fatalErrorStatus")

        copy_button = QPushButton("Copy incident reference")
        copy_button.setObjectName("copyIncidentReferenceButton")
        copy_button.clicked.connect(self._copy_reference)
        open_logs_button = QPushButton("Open log folder")
        open_logs_button.setObjectName("openLogDirectoryButton")
        open_logs_button.setEnabled(log_directory is not None)
        open_logs_button.clicked.connect(self._open_log_directory)
        close_button = QPushButton("Close application")
        close_button.setObjectName("closeApplicationButton")
        close_button.clicked.connect(self.accept)

        actions = QHBoxLayout()
        actions.addWidget(copy_button)
        actions.addWidget(open_logs_button)
        actions.addStretch(1)
        actions.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(explanation)
        layout.addWidget(reference)
        layout.addWidget(phase)
        layout.addWidget(timestamp)
        layout.addWidget(self._status)
        layout.addLayout(actions)

    def safe_clipboard_text(self) -> str:
        """Return the complete bounded text allowed onto the clipboard."""
        return (
            f"{config.APP_NAME} incident\n"
            f"Reference: {self._incident.incident_id}\n"
            f"Phase: {_PHASE_LABELS[self._incident.phase.value]}\n"
            f"UTC time: {self._incident.occurred_at_utc.isoformat()}"
        )

    def _copy_reference(self) -> None:
        try:
            QApplication.clipboard().setText(self.safe_clipboard_text())
            self._status.setText("Incident reference copied.")
        except Exception:
            self._status.setText("The incident reference could not be copied.")

    def _open_log_directory(self) -> None:
        if self._log_directory is None:
            return
        try:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._log_directory)))
        except Exception:
            opened = False
        self._status.setText(
            "Log folder opened." if opened else "The log folder could not be opened."
        )


__all__ = ["FatalErrorDialog"]
