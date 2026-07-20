"""Presentation-only Portfolio valuation panel."""

from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget

from app.application.results import PortfolioDashboard
from app.ui.components.widgets import CardWidget, ModernTable, SecondaryButton, SectionTitle
from app.ui.theme.tokens import Colors, Spacing

_ZERO = Decimal("0")
_MISSING_VALUE = "—"
_ASSET_TYPE_LABELS = {
    "equity": "Equity",
    "fund": "Fund",
    "etf": "ETF",
    "bond": "Bond",
    "crypto": "Crypto",
    "cash": "Cash",
}


def format_decimal(value: Decimal) -> str:
    """Format an exact Decimal in fixed-point notation without quantization."""
    return format(value, "f")


def format_optional_decimal(value: Decimal | None) -> str:
    """Format an optional Decimal without substituting a financial value."""
    return _MISSING_VALUE if value is None else format_decimal(value)


def format_optional_timestamp(value: datetime | None) -> str:
    """Format a supplied timestamp without timezone conversion."""
    return _MISSING_VALUE if value is None else f"{value.strftime('%Y-%m-%d %H:%M:%S')} UTC"


class PortfolioValuationPanel(CardWidget):
    """Render calculated application DTOs without financial calculations."""

    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.SM)

        header = QHBoxLayout()
        header.addWidget(SectionTitle("Calculated valuation", self))
        header.addStretch()
        self._refresh_button = SecondaryButton("Refresh valuation", self)
        self._refresh_button.setObjectName("refreshValuationButton")
        self._refresh_button.setStyleSheet(
            f"background: {Colors.SURFACE_RAISED}; border: 1px solid {Colors.BORDER};"
        )
        self._refresh_button.clicked.connect(self.refresh_requested.emit)
        header.addWidget(self._refresh_button)
        layout.addLayout(header)

        self._state_label = QLabel(
            "Create or select a portfolio to view valuation.",
            self,
        )
        self._state_label.setObjectName("valuationState")
        self._state_label.setWordWrap(True)
        self._state_label.setStyleSheet(f"color: {Colors.WARNING};")
        layout.addWidget(self._state_label)

        self._no_fx_label = QLabel(
            "Values are shown separately by currency. No FX conversion is applied.",
            self,
        )
        self._no_fx_label.setObjectName("noFxNotice")
        self._no_fx_label.setWordWrap(True)
        self._no_fx_label.setStyleSheet(f"color: {Colors.MUTED};")
        layout.addWidget(self._no_fx_label)

        table_splitter = QSplitter(Qt.Orientation.Vertical, self)
        table_splitter.setObjectName("valuationTableSplitter")
        self._position_table = ModernTable(
            [
                "Asset",
                "Type",
                "Currency",
                "Status",
                "Quantity",
                "Average cost",
                "Cost basis",
                "Market price",
                "Market value",
                "Realized P&L",
                "Unrealized P&L",
                "Total P&L",
                "Price observed",
            ],
            table_splitter,
        )
        self._position_table.setObjectName("positionTable")
        self._position_table.setMinimumHeight(150)
        table_splitter.addWidget(self._position_table)

        self._currency_table = ModernTable(
            [
                "Currency",
                "Cost basis",
                "Market value",
                "Realized P&L",
                "Unrealized P&L",
                "Total P&L",
            ],
            table_splitter,
        )
        self._currency_table.setObjectName("currencyValuationTable")
        self._currency_table.setMinimumHeight(110)
        table_splitter.addWidget(self._currency_table)
        table_splitter.setStretchFactor(0, 2)
        table_splitter.setStretchFactor(1, 1)
        layout.addWidget(table_splitter, 1)

    def show_dashboard(self, dashboard: PortfolioDashboard) -> None:
        """Render one complete calculated dashboard in DTO order."""
        self._clear_tables()
        for position in dashboard.positions:
            row = self._position_table.add_row(
                (
                    f"{position.symbol} — {position.name}",
                    _ASSET_TYPE_LABELS[position.asset_type.value],
                    position.currency.value,
                    "Closed" if position.quantity == _ZERO else "Open",
                    format_decimal(position.quantity),
                    format_decimal(position.average_cost),
                    format_decimal(position.cost_basis),
                    format_optional_decimal(position.market_price),
                    format_decimal(position.market_value),
                    format_decimal(position.realized_pnl),
                    format_decimal(position.unrealized_pnl),
                    format_decimal(position.total_pnl),
                    format_optional_timestamp(position.price_observed_at),
                )
            )
            asset_item = self._position_table.item(row, 0)
            if asset_item is not None:
                asset_item.setData(Qt.ItemDataRole.UserRole, str(position.asset_id))

        for valuation in dashboard.currencies:
            self._currency_table.add_row(
                (
                    valuation.currency.value,
                    format_decimal(valuation.cost_basis),
                    format_decimal(valuation.market_value),
                    format_decimal(valuation.realized_pnl),
                    format_decimal(valuation.unrealized_pnl),
                    format_decimal(valuation.total_pnl),
                )
            )

        if dashboard.positions or dashboard.currencies:
            self._state_label.hide()
        else:
            self.show_empty("No calculated positions yet. Record a BUY transaction to begin.")

    def show_empty(self, message: str) -> None:
        """Clear stale valuation rows and show a truthful empty state."""
        self._clear_tables()
        self._state_label.setText(message)
        self._state_label.show()
        self._state_label.setStyleSheet(f"color: {Colors.WARNING};")

    def show_missing_price(self, message: str) -> None:
        """Clear stale valuation rows and show an actionable missing-price state."""
        self.show_empty(message)

    def clear(self) -> None:
        """Clear valuation state without inventing replacement values."""
        self.show_empty("Create or select a portfolio to view valuation.")

    def set_refresh_enabled(self, enabled: bool) -> None:
        """Set manual-refresh availability from MainWindow orchestration state."""
        self._refresh_button.setEnabled(enabled)

    def _clear_tables(self) -> None:
        self._position_table.clearContents()
        self._position_table.setRowCount(0)
        self._currency_table.clearContents()
        self._currency_table.setRowCount(0)


__all__ = [
    "PortfolioValuationPanel",
    "format_decimal",
    "format_optional_decimal",
    "format_optional_timestamp",
]
