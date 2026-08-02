"""Shared offscreen Qt fixtures for UI unit tests."""

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.infrastructure.resilience import GlobalErrorBoundary  # noqa: E402
from app.ui.application import MiraApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapplication() -> Iterator[QApplication]:
    """Provide the process-wide QApplication without requiring a display."""
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
