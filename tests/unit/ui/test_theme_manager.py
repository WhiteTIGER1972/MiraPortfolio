"""Supported-theme validation and safe reapplication tests."""

import pytest
from PySide6.QtWidgets import QApplication

from app.ui.theme.manager import ThemeManager


def test_dark_theme_reapplication_is_idempotent(qapplication: QApplication) -> None:
    ThemeManager.apply(qapplication, "dark")
    first_stylesheet = qapplication.styleSheet()

    ThemeManager.apply(qapplication, "dark")

    assert qapplication.styleSheet() == first_stylesheet


def test_unsupported_theme_is_rejected_before_application_changes(
    qapplication: QApplication,
) -> None:
    ThemeManager.apply(qapplication, "dark")
    existing_stylesheet = qapplication.styleSheet()

    with pytest.raises(ValueError, match="unsupported"):
        ThemeManager.apply(qapplication, "light")

    assert qapplication.styleSheet() == existing_stylesheet
