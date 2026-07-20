"""Presentation tests for calculated Portfolio valuation DTOs."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLabel,
    QPushButton,
    QTableWidget,
)

from app.application.results import (
    CurrencyValuationView,
    PortfolioDashboard,
    PortfolioDetails,
    ValuedAssetPositionView,
)
from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency
from app.ui.components import portfolio_valuation_panel as panel_module
from app.ui.components.portfolio_valuation_panel import PortfolioValuationPanel

CREATED_AT = datetime(2026, 7, 18, 9, 30, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 7, 20, 12, 34, 56, 789000, tzinfo=UTC)


def empty_details() -> PortfolioDetails:
    """Build the nested dashboard Portfolio identity."""
    return PortfolioDetails(
        id=uuid4(),
        name="Valuation",
        base_currency=Currency.TRY,
        assets=(),
        transactions=(),
        is_archived=False,
        created_at=CREATED_AT,
    )


def valued_position(
    symbol: str,
    name: str,
    *,
    asset_id=None,
    currency: Currency = Currency.TRY,
    quantity: str = "2.5000",
    average_cost: str = "100.000000000000000001",
    cost_basis: str = "250.0000000000000000025",
    market_price: str | None = "123.4500",
    market_value: str = "308.6250000000000000000",
    realized_pnl: str = "7.0000",
    unrealized_pnl: str = "58.6250000000000000000",
    total_pnl: str = "65.6250000000000000000",
    observed_at: datetime | None = OBSERVED_AT,
) -> ValuedAssetPositionView:
    """Build intentionally nontrivial calculated values."""
    return ValuedAssetPositionView(
        asset_id=asset_id or uuid4(),
        symbol=symbol,
        name=name,
        asset_type=AssetType.EQUITY,
        currency=currency,
        quantity=Decimal(quantity),
        average_cost=Decimal(average_cost),
        cost_basis=Decimal(cost_basis),
        market_price=Decimal(market_price) if market_price is not None else None,
        price_observed_at=observed_at,
        market_value=Decimal(market_value),
        realized_pnl=Decimal(realized_pnl),
        unrealized_pnl=Decimal(unrealized_pnl),
        total_pnl=Decimal(total_pnl),
    )


def currency_valuation(
    currency: Currency,
    *,
    cost_basis: str,
    market_value: str,
    realized_pnl: str,
    unrealized_pnl: str,
    total_pnl: str,
) -> CurrencyValuationView:
    """Build one exact per-currency DTO row."""
    return CurrencyValuationView(
        currency=currency,
        cost_basis=Decimal(cost_basis),
        market_value=Decimal(market_value),
        realized_pnl=Decimal(realized_pnl),
        unrealized_pnl=Decimal(unrealized_pnl),
        total_pnl=Decimal(total_pnl),
    )


def test_panel_empty_state_and_refresh_control(qapplication: QApplication) -> None:
    panel = PortfolioValuationPanel()
    requests: list[bool] = []
    panel.refresh_requested.connect(lambda: requests.append(True))
    try:
        position_table = panel.findChild(QTableWidget, "positionTable")
        currency_table = panel.findChild(QTableWidget, "currencyValuationTable")
        state = panel.findChild(QLabel, "valuationState")
        refresh = panel.findChild(QPushButton, "refreshValuationButton")
        dashboard = PortfolioDashboard(portfolio=empty_details(), positions=(), currencies=())

        panel.show_dashboard(dashboard)

        assert position_table.rowCount() == 0
        assert currency_table.rowCount() == 0
        assert state.text() == "No calculated positions yet. Record a BUY transaction to begin."
        assert position_table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        assert currency_table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        panel.set_refresh_enabled(True)
        QTest.mouseClick(refresh, Qt.MouseButton.LeftButton)
        assert requests == [True]
        panel.set_refresh_enabled(False)
        assert not refresh.isEnabled()
    finally:
        panel.close()
        panel.deleteLater()
        qapplication.processEvents()


def test_panel_maps_every_open_position_field_exactly(
    qapplication: QApplication,
) -> None:
    panel = PortfolioValuationPanel()
    position = valued_position("OPEN", "Open Asset")
    dashboard = PortfolioDashboard(
        portfolio=empty_details(),
        positions=(position,),
        currencies=(),
    )
    try:
        panel.show_dashboard(dashboard)
        table = panel.findChild(QTableWidget, "positionTable")

        assert table.rowCount() == 1
        assert [table.item(0, column).text() for column in range(13)] == [
            "OPEN — Open Asset",
            "Equity",
            "TRY",
            "Open",
            "2.5000",
            "100.000000000000000001",
            "250.0000000000000000025",
            "123.4500",
            "308.6250000000000000000",
            "7.0000",
            "58.6250000000000000000",
            "65.6250000000000000000",
            "2026-07-20 12:34:56 UTC",
        ]
        assert table.item(0, 0).data(Qt.ItemDataRole.UserRole) == str(position.asset_id)
        assert [
            table.horizontalHeaderItem(column).text() for column in range(table.columnCount())
        ] == [
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
        ]
    finally:
        panel.close()
        panel.deleteLater()
        qapplication.processEvents()


def test_panel_keeps_closed_position_with_missing_price_markers(
    qapplication: QApplication,
) -> None:
    panel = PortfolioValuationPanel()
    closed = valued_position(
        "CLOSED",
        "Closed Asset",
        quantity="0",
        average_cost="0",
        cost_basis="0",
        market_price=None,
        market_value="0",
        realized_pnl="42.1200",
        unrealized_pnl="0",
        total_pnl="42.1200",
        observed_at=None,
    )
    try:
        panel.show_dashboard(
            PortfolioDashboard(
                portfolio=empty_details(),
                positions=(closed,),
                currencies=(),
            )
        )
        table = panel.findChild(QTableWidget, "positionTable")

        assert table.rowCount() == 1
        assert table.item(0, 3).text() == "Closed"
        assert table.item(0, 7).text() == "—"
        assert table.item(0, 8).text() == "0"
        assert table.item(0, 9).text() == "42.1200"
        assert table.item(0, 10).text() == "0"
        assert table.item(0, 11).text() == "42.1200"
        assert table.item(0, 12).text() == "—"
    finally:
        panel.close()
        panel.deleteLater()
        qapplication.processEvents()


def test_panel_preserves_duplicate_symbols_zero_price_and_uuid_rows(
    qapplication: QApplication,
) -> None:
    panel = PortfolioValuationPanel()
    first = valued_position(
        "DUP",
        "First",
        currency=Currency.TRY,
        market_price="0",
    )
    second = valued_position(
        "DUP",
        "Second",
        currency=Currency.USD,
        market_price="2.0000",
    )
    try:
        panel.show_dashboard(
            PortfolioDashboard(
                portfolio=empty_details(),
                positions=(first, second),
                currencies=(),
            )
        )
        table = panel.findChild(QTableWidget, "positionTable")

        assert table.rowCount() == 2
        assert [table.item(row, 0).text() for row in range(2)] == [
            "DUP — First",
            "DUP — Second",
        ]
        assert table.item(0, 7).text() == "0"
        assert table.item(0, 12).text() == "2026-07-20 12:34:56 UTC"
        assert table.item(0, 0).data(Qt.ItemDataRole.UserRole) == str(first.asset_id)
        assert table.item(1, 0).data(Qt.ItemDataRole.UserRole) == str(second.asset_id)
        assert table.item(0, 0).data(Qt.ItemDataRole.UserRole) != table.item(1, 0).data(
            Qt.ItemDataRole.UserRole
        )
    finally:
        panel.close()
        panel.deleteLater()
        qapplication.processEvents()


def test_panel_preserves_currency_order_without_grand_total_and_clears_stale_rows(
    qapplication: QApplication,
) -> None:
    panel = PortfolioValuationPanel()
    usd = currency_valuation(
        Currency.USD,
        cost_basis="1.000000000000000001",
        market_value="2.000000000000000002",
        realized_pnl="3.0000",
        unrealized_pnl="4.0000",
        total_pnl="99.9900",
    )
    eur = currency_valuation(
        Currency.EUR,
        cost_basis="5.5000",
        market_value="6.6000",
        realized_pnl="-7.7000",
        unrealized_pnl="8.8000",
        total_pnl="1.1000",
    )
    try:
        panel.show_dashboard(
            PortfolioDashboard(
                portfolio=empty_details(),
                positions=(valued_position("ONE", "One"),),
                currencies=(usd, eur),
            )
        )
        table = panel.findChild(QTableWidget, "currencyValuationTable")
        no_fx = panel.findChild(QLabel, "noFxNotice")

        assert table.rowCount() == 2
        assert [table.item(row, 0).text() for row in range(2)] == ["USD", "EUR"]
        assert [table.item(0, column).text() for column in range(1, 6)] == [
            "1.000000000000000001",
            "2.000000000000000002",
            "3.0000",
            "4.0000",
            "99.9900",
        ]
        assert no_fx.text() == (
            "Values are shown separately by currency. No FX conversion is applied."
        )
        visible_text = " ".join(label.text() for label in panel.findChildren(QLabel))
        assert "Total Portfolio Value" not in visible_text
        assert table.rowCount() == len((usd, eur))

        panel.clear()
        assert panel.findChild(QTableWidget, "positionTable").rowCount() == 0
        assert table.rowCount() == 0
        assert panel.findChild(QLabel, "valuationState").text() == (
            "Create or select a portfolio to view valuation."
        )
    finally:
        panel.close()
        panel.deleteLater()
        qapplication.processEvents()


def test_panel_source_is_presentation_only_without_financial_formulas() -> None:
    source = Path(panel_module.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "Container",
        "app.application.services",
        "app.infrastructure",
        "sqlalchemy",
        "UnitOfWork",
        "float(",
        "round(",
        "quantize(",
        "quantity *",
        "realized_pnl +",
        "sum(",
        "grand_total",
        "portfolio_total",
        "fx_rate",
    ):
        assert forbidden not in source
