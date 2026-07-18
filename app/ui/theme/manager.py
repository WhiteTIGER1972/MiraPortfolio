"""Qt style sheet theme management."""

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app.ui.theme.tokens import Colors


class ThemeManager:
    """Apply the application-wide visual language."""

    @staticmethod
    def apply(application: QApplication) -> None:
        """Apply typography and a polished dark stylesheet."""
        application.setFont(QFont("Inter", 10))
        application.setStyleSheet(
            f"\n* {{ color: {Colors.TEXT}; font-family: Inter, Segoe UI, sans-serif; }}\n"
            f"QMainWindow, QWidget#central {{ background: {Colors.BACKGROUND}; }}\n"
            f"QToolBar {{ background: {Colors.BACKGROUND}; border: none; "
            "spacing: 12px; padding: 12px 24px; }\n"
            f"QStatusBar {{ background: {Colors.SURFACE}; "
            f"border-top: 1px solid {Colors.BORDER}; color: {Colors.MUTED}; }}\n"
            "QPushButton { border: none; border-radius: 9px; padding: 9px 16px; "
            "font-weight: 600; }\n"
            f"QPushButton#primary {{ background: {Colors.ACCENT}; }} "
            f"QPushButton#primary:hover {{ background: {Colors.ACCENT_HOVER}; }}\n"
            f"QPushButton#secondary {{ background: {Colors.SURFACE_RAISED}; "
            f"border: 1px solid {Colors.BORDER}; }}\n"
            "QPushButton#secondary:hover { background: #222938; }\n"
            f"QTableWidget {{ background: {Colors.SURFACE}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 12px; "
            f"gridline-color: {Colors.BORDER}; "
            "selection-background-color: #272250; }\n"
            f"QHeaderView::section {{ background: {Colors.SURFACE_RAISED}; "
            f"color: {Colors.MUTED}; border: none; padding: 11px; "
            "font-weight: 700; }\n"
        )
