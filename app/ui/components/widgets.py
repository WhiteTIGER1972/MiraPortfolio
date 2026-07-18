"""Reusable design-system widgets."""

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme.tokens import Colors, Elevation, Radius, Spacing, Typography


class PrimaryButton(QPushButton):
    """High-emphasis action button."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("primary")


class SecondaryButton(QPushButton):
    """Low-emphasis action button."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("secondary")


class CardWidget(QFrame):
    """Elevated rounded content container."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"background: {Colors.SURFACE}; border: 1px solid {Colors.BORDER}; "
            f"border-radius: {Radius.LARGE}px;"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(Elevation.CARD_BLUR)
        shadow.setOffset(0, Elevation.CARD_OFFSET)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)


class SectionTitle(QLabel):
    """Standard section heading label."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setStyleSheet(f"font-size: {Typography.HEADING}px; font-weight: 700;")


class MetricCard(CardWidget):
    """Card presenting a financial metric."""

    def __init__(self, label: str, value: str, change: str, positive: bool = True) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.MD, Spacing.MD, Spacing.MD, Spacing.MD)
        layout.setSpacing(Spacing.SM)
        title = QLabel(label.upper())
        title.setStyleSheet(
            f"color: {Colors.MUTED}; font-size: {Typography.CAPTION}px; font-weight: 700;"
        )
        amount = QLabel(value)
        amount.setStyleSheet("font-size: 23px; font-weight: 700;")
        trend = QLabel(change)
        trend.setStyleSheet(
            f"color: {Colors.POSITIVE if positive else Colors.NEGATIVE}; font-weight: 700;"
        )
        layout.addWidget(title)
        layout.addWidget(amount)
        layout.addWidget(trend)


class ModernTable(QTableWidget):
    """Styled read-only table for compact portfolio data."""

    def __init__(self, headers: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(list(headers))
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setShowGrid(False)
        self.verticalHeader().hide()
        self.horizontalHeader().setStretchLastSection(True)

    def add_row(self, values: Sequence[str]) -> None:
        """Append a row of display values."""
        row = self.rowCount()
        self.insertRow(row)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self.setItem(row, column, item)
