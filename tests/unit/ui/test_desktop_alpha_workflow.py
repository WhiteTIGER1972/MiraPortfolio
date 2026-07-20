"""Presentation-level acceptance tests for the complete Desktop Alpha workflow."""

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, cast
from uuid import UUID

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from app.application.commands import (
    BuyAssetCommand,
    CreateAssetCommand,
    CreatePortfolioCommand,
    DeleteTransactionCommand,
    RecordMarketPriceCommand,
    SellAssetCommand,
)
from app.application.exceptions import ValidationError
from app.application.queries import (
    GetLatestMarketPriceQuery,
    GetPortfolioDashboardQuery,
    GetPortfolioQuery,
    ListAssetsQuery,
    ListPortfoliosQuery,
)
from app.application.results import (
    AssetPositionView,
    AssetView,
    CurrencyValuationView,
    MarketPriceView,
    PortfolioDashboard,
    PortfolioDetails,
    PortfolioSummary,
    TransactionView,
    ValuedAssetPositionView,
)
from app.core.container import Container
from app.domain.entities.asset import AssetType
from app.domain.entities.transaction import TransactionType
from app.domain.exceptions import MissingMarketPriceError
from app.domain.value_objects.currency import Currency
from app.ui.dialogs import RecordTradeDialog
from app.ui.windows import main_window as main_window_module
from app.ui.windows.main_window import MainWindow

CREATED_AT = datetime(2026, 7, 18, 9, 30, tzinfo=UTC)
BUY_AT = datetime(2026, 7, 20, 10, 15, 30, tzinfo=UTC)
PRICE_AT = datetime(2026, 7, 20, 11, 45, 15, tzinfo=UTC)
SELL_AT = datetime(2026, 7, 21, 14, 5, 45, tzinfo=UTC)

PORTFOLIO_ID = UUID("10000000-0000-0000-0000-000000000001")
ASSET_ID = UUID("20000000-0000-0000-0000-000000000001")
BUY_ID = UUID("30000000-0000-0000-0000-000000000001")
SELL_ID = UUID("30000000-0000-0000-0000-000000000002")
PRICE_ID = UUID("40000000-0000-0000-0000-000000000001")
EM_DASH = "\u2014"

PORTFOLIO_SUMMARY = PortfolioSummary(
    id=PORTFOLIO_ID,
    name="Alpha",
    base_currency=Currency.TRY,
    is_archived=False,
    created_at=CREATED_AT,
)
ASSET = AssetView(
    id=ASSET_ID,
    symbol="FLOW",
    name="Workflow Asset",
    asset_type=AssetType.EQUITY,
    currency=Currency.TRY,
    is_active=True,
    created_at=CREATED_AT,
)
POSITION_ASSET = AssetPositionView(
    id=ASSET.id,
    symbol=ASSET.symbol,
    name=ASSET.name,
    asset_type=ASSET.asset_type,
    currency=ASSET.currency,
    is_active=ASSET.is_active,
    created_at=ASSET.created_at,
)
BUY_TRANSACTION = TransactionView(
    id=BUY_ID,
    asset_id=ASSET.id,
    quantity=Decimal("10.000000000000000001"),
    price=Decimal("100.123400000000000001"),
    transaction_type=TransactionType.BUY,
    commission=Decimal("1.2300"),
    tax=Decimal("0.0400"),
    date=BUY_AT,
)
SELL_TRANSACTION = TransactionView(
    id=SELL_ID,
    asset_id=ASSET.id,
    quantity=Decimal("3.250000000000000000"),
    price=Decimal("130.567800000000000001"),
    transaction_type=TransactionType.SELL,
    commission=Decimal("1.1100"),
    tax=Decimal("0.2200"),
    date=SELL_AT,
)
EMPTY_DETAILS = PortfolioDetails(
    id=PORTFOLIO_ID,
    name="Alpha",
    base_currency=Currency.TRY,
    assets=(),
    transactions=(),
    is_archived=False,
    created_at=CREATED_AT,
)
AFTER_BUY_DETAILS = PortfolioDetails(
    id=PORTFOLIO_ID,
    name="Alpha",
    base_currency=Currency.TRY,
    assets=(POSITION_ASSET,),
    transactions=(BUY_TRANSACTION,),
    is_archived=False,
    created_at=CREATED_AT,
)
AFTER_SELL_DETAILS = PortfolioDetails(
    id=PORTFOLIO_ID,
    name="Alpha",
    base_currency=Currency.TRY,
    assets=(POSITION_ASSET,),
    transactions=(BUY_TRANSACTION, SELL_TRANSACTION),
    is_archived=False,
    created_at=CREATED_AT,
)


def valued_position(
    asset: AssetView,
    *,
    quantity: str,
    average_cost: str,
    cost_basis: str,
    market_price: str | None,
    price_observed_at: datetime | None,
    market_value: str,
    realized_pnl: str,
    unrealized_pnl: str,
    total_pnl: str,
) -> ValuedAssetPositionView:
    """Build one controlled valuation DTO without deriving any field."""
    return ValuedAssetPositionView(
        asset_id=asset.id,
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type,
        currency=asset.currency,
        quantity=Decimal(quantity),
        average_cost=Decimal(average_cost),
        cost_basis=Decimal(cost_basis),
        market_price=Decimal(market_price) if market_price is not None else None,
        price_observed_at=price_observed_at,
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
    """Build one controlled currency DTO without aggregating any field."""
    return CurrencyValuationView(
        currency=currency,
        cost_basis=Decimal(cost_basis),
        market_value=Decimal(market_value),
        realized_pnl=Decimal(realized_pnl),
        unrealized_pnl=Decimal(unrealized_pnl),
        total_pnl=Decimal(total_pnl),
    )


BUY_POSITION = valued_position(
    ASSET,
    quantity="10.000000000000000001",
    average_cost="100.123400000000000001",
    cost_basis="1001.234000000000000010",
    market_price="123.456700",
    price_observed_at=PRICE_AT,
    market_value="1234.567000000000000123",
    realized_pnl="0.1250",
    unrealized_pnl="233.333000000000000113",
    total_pnl="999.999000000000000777",
)
BUY_CURRENCY = currency_valuation(
    Currency.TRY,
    cost_basis="1001.234000000000000010",
    market_value="1234.567000000000000123",
    realized_pnl="0.1250",
    unrealized_pnl="233.333000000000000113",
    total_pnl="999.999000000000000777",
)
SELL_POSITION = valued_position(
    ASSET,
    quantity="6.750000000000000001",
    average_cost="100.123400000000000001",
    cost_basis="675.832950000000000100",
    market_price="130.0000",
    price_observed_at=SELL_AT,
    market_value="877.500000000000000130",
    realized_pnl="77.777700000000000007",
    unrealized_pnl="201.667050000000000030",
    total_pnl="314.159265358979323846",
)
SELL_CURRENCY = currency_valuation(
    Currency.TRY,
    cost_basis="675.832950000000000100",
    market_value="877.500000000000000130",
    realized_pnl="77.777700000000000007",
    unrealized_pnl="201.667050000000000030",
    total_pnl="314.159265358979323846",
)
EMPTY_DASHBOARD = PortfolioDashboard(
    portfolio=EMPTY_DETAILS,
    positions=(),
    currencies=(),
)
BUY_DASHBOARD = PortfolioDashboard(
    portfolio=AFTER_BUY_DETAILS,
    positions=(BUY_POSITION,),
    currencies=(BUY_CURRENCY,),
)
SELL_DASHBOARD = PortfolioDashboard(
    portfolio=AFTER_SELL_DETAILS,
    positions=(SELL_POSITION,),
    currencies=(SELL_CURRENCY,),
)
RECORDED_PRICE = MarketPriceView(
    id=PRICE_ID,
    asset_id=ASSET.id,
    price=Decimal("123.456700"),
    currency=ASSET.currency,
    observed_at=PRICE_AT,
)


class DesktopAlphaHarness:
    """Stateful fake service boundary with explicitly supplied DTO transitions."""

    def __init__(
        self,
        *,
        portfolios: tuple[PortfolioSummary, ...] = (),
        details: PortfolioDetails | None = None,
        assets: tuple[AssetView, ...] = (),
        dashboard: PortfolioDashboard | Exception | None = None,
    ) -> None:
        self.portfolios = portfolios
        self.details = details
        self.assets = assets
        self.dashboard_result = dashboard

        self.portfolio_after_create = EMPTY_DETAILS
        self.asset_after_create = ASSET
        self.buy_result = BUY_TRANSACTION
        self.details_after_buy = AFTER_BUY_DETAILS
        self.dashboard_after_buy: PortfolioDashboard | Exception = MissingMarketPriceError(ASSET.id)
        self.price_result = RECORDED_PRICE
        self.dashboard_after_price: PortfolioDashboard | Exception = BUY_DASHBOARD
        self.sell_result = SELL_TRANSACTION
        self.details_after_sell = AFTER_SELL_DETAILS
        self.dashboard_after_sell: PortfolioDashboard | Exception = SELL_DASHBOARD
        self.details_after_delete = AFTER_BUY_DETAILS
        self.dashboard_after_delete: PortfolioDashboard | Exception = BUY_DASHBOARD

        self.sell_error: Exception | None = None

        self.list_portfolio_calls: list[ListPortfoliosQuery] = []
        self.get_portfolio_calls: list[GetPortfolioQuery] = []
        self.create_portfolio_calls: list[CreatePortfolioCommand] = []
        self.list_asset_calls: list[ListAssetsQuery] = []
        self.create_asset_calls: list[CreateAssetCommand] = []
        self.buy_calls: list[BuyAssetCommand] = []
        self.sell_calls: list[SellAssetCommand] = []
        self.delete_calls: list[DeleteTransactionCommand] = []
        self.record_price_calls: list[RecordMarketPriceCommand] = []
        self.latest_price_calls: list[GetLatestMarketPriceQuery] = []
        self.dashboard_calls: list[GetPortfolioDashboardQuery] = []

    def list_portfolios(
        self,
        query: ListPortfoliosQuery,
    ) -> tuple[PortfolioSummary, ...]:
        self.list_portfolio_calls.append(query)
        return self.portfolios

    def get_portfolio(self, query: GetPortfolioQuery) -> PortfolioDetails:
        self.get_portfolio_calls.append(query)
        if self.details is None or self.details.id != query.portfolio_id:
            raise AssertionError("No controlled PortfolioDetails exists for this UUID.")
        return self.details

    def create_portfolio(self, command: CreatePortfolioCommand) -> PortfolioDetails:
        self.create_portfolio_calls.append(command)
        created = self.portfolio_after_create
        self.portfolios = (
            PortfolioSummary(
                id=created.id,
                name=created.name,
                base_currency=created.base_currency,
                is_archived=created.is_archived,
                created_at=created.created_at,
            ),
        )
        self.details = created
        self.dashboard_result = EMPTY_DASHBOARD
        return created

    def list_assets(self, query: ListAssetsQuery) -> tuple[AssetView, ...]:
        self.list_asset_calls.append(query)
        return self.assets

    def create_asset(self, command: CreateAssetCommand) -> AssetView:
        self.create_asset_calls.append(command)
        created = self.asset_after_create
        self.assets = (*self.assets, created)
        return created

    def buy_asset(self, command: BuyAssetCommand) -> TransactionView:
        self.buy_calls.append(command)
        self.details = self.details_after_buy
        self.dashboard_result = self.dashboard_after_buy
        return self.buy_result

    def sell_asset(self, command: SellAssetCommand) -> TransactionView:
        self.sell_calls.append(command)
        if self.sell_error is not None:
            raise self.sell_error
        self.details = self.details_after_sell
        self.dashboard_result = self.dashboard_after_sell
        return self.sell_result

    def delete_transaction(self, command: DeleteTransactionCommand) -> PortfolioDetails:
        self.delete_calls.append(command)
        self.details = self.details_after_delete
        self.dashboard_result = self.dashboard_after_delete
        return self.details_after_delete

    def record_market_price(self, command: RecordMarketPriceCommand) -> MarketPriceView:
        self.record_price_calls.append(command)
        self.dashboard_result = self.dashboard_after_price
        return self.price_result

    def get_latest_market_price(
        self,
        query: GetLatestMarketPriceQuery,
    ) -> MarketPriceView | None:
        self.latest_price_calls.append(query)
        raise AssertionError("Desktop Alpha UI must not read latest prices directly.")

    def get_dashboard(self, query: GetPortfolioDashboardQuery) -> PortfolioDashboard:
        self.dashboard_calls.append(query)
        result = self.dashboard_result
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise AssertionError("No controlled PortfolioDashboard exists for this state.")
        return result


class DesktopWindowFactory:
    """Open and release MainWindows over a shared fake service state."""

    def __init__(self, application: QApplication) -> None:
        self._application = application
        self._windows: list[MainWindow] = []

    def __call__(self, harness: DesktopAlphaHarness) -> MainWindow:
        container = cast(
            Container,
            SimpleNamespace(
                settings=SimpleNamespace(app_name="Mira Desktop Alpha Acceptance"),
                portfolio_application_service=harness,
                asset_application_service=harness,
                market_price_application_service=harness,
                portfolio_dashboard_query_service=harness,
            ),
        )
        window = MainWindow(container)
        self._windows.append(window)
        return window

    def close_all(self) -> None:
        """Release every window created by an acceptance test."""
        for window in self._windows:
            window.close()
            window.deleteLater()
        self._application.processEvents()


@pytest.fixture
def open_desktop_window(
    qapplication: QApplication,
) -> Iterator[DesktopWindowFactory]:
    """Provide tracked offscreen MainWindows."""
    factory = DesktopWindowFactory(qapplication)
    yield factory
    factory.close_all()


def child[WidgetT: QObject](
    parent: QObject,
    widget_type: type[WidgetT],
    object_name: str,
) -> WidgetT:
    """Return one required Qt child with a narrowed non-optional type."""
    widget = parent.findChild(widget_type, object_name)
    assert widget is not None
    return widget


def cell(table: QTableWidget, row: int, column: int) -> QTableWidgetItem:
    """Return one required populated table cell."""
    item = table.item(row, column)
    assert item is not None
    return item


class AcceptedPortfolioDialog:
    """Supply the accepted Portfolio creation input."""

    expected_parent: ClassVar[MainWindow | None] = None

    def __init__(self, parent: MainWindow) -> None:
        assert parent is self.expected_parent

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def portfolio_name(self) -> str:
        return "Alpha"


class AcceptedAssetDialog:
    """Supply the accepted Asset creation input."""

    expected_parent: ClassVar[MainWindow | None] = None

    def __init__(self, parent: MainWindow) -> None:
        assert parent is self.expected_parent

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def symbol(self) -> str:
        return ASSET.symbol

    def asset_name(self) -> str:
        return ASSET.name

    def asset_type(self) -> AssetType:
        return ASSET.asset_type

    def currency(self) -> Currency:
        return ASSET.currency


class ScriptedTradeDialog:
    """Supply one exact accepted BUY or SELL payload."""

    expected_parent: ClassVar[MainWindow | None] = None
    expected_type: ClassVar[TransactionType] = TransactionType.BUY
    expected_assets: ClassVar[tuple[AssetView | AssetPositionView, ...]] = ()
    selected_asset_id: ClassVar[UUID] = ASSET_ID
    entered_quantity: ClassVar[Decimal] = Decimal("0")
    entered_price: ClassVar[Decimal] = Decimal("0")
    entered_at: ClassVar[datetime] = BUY_AT
    entered_commission: ClassVar[Decimal] = Decimal("0")
    entered_tax: ClassVar[Decimal] = Decimal("0")

    def __init__(
        self,
        transaction_type: TransactionType,
        assets: Sequence[AssetView | AssetPositionView],
        parent: MainWindow,
    ) -> None:
        assert parent is self.expected_parent
        assert transaction_type is self.expected_type
        assert tuple(assets) == self.expected_assets

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def asset_id(self) -> UUID:
        return self.selected_asset_id

    def quantity(self) -> Decimal:
        return self.entered_quantity

    def unit_price(self) -> Decimal:
        return self.entered_price

    def trade_datetime(self) -> datetime:
        return self.entered_at

    def commission(self) -> Decimal:
        return self.entered_commission

    def tax(self) -> Decimal:
        return self.entered_tax


class RejectedTradeDialog(ScriptedTradeDialog):
    """Cancel a trade without exposing command values."""

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Rejected


class ScriptedPriceDialog:
    """Supply one exact accepted market-price payload."""

    expected_parent: ClassVar[MainWindow | None] = None
    expected_assets: ClassVar[tuple[AssetView, ...]] = ()
    selected_asset_id: ClassVar[UUID] = ASSET_ID
    entered_price: ClassVar[Decimal] = Decimal("0")
    entered_at: ClassVar[datetime] = PRICE_AT

    def __init__(self, assets: Sequence[AssetView], parent: MainWindow) -> None:
        assert parent is self.expected_parent
        assert tuple(assets) == self.expected_assets

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def asset_id(self) -> UUID:
        return self.selected_asset_id

    def price(self) -> Decimal:
        return self.entered_price

    def observed_at(self) -> datetime:
        return self.entered_at


class RejectedPriceDialog(ScriptedPriceDialog):
    """Cancel a price write without exposing command values."""

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Rejected


def configure_trade(
    window: MainWindow,
    transaction_type: TransactionType,
    assets: tuple[AssetView | AssetPositionView, ...],
    *,
    asset_id: UUID,
    quantity: str,
    price: str,
    occurred_at: datetime,
    commission: str,
    tax: str,
) -> None:
    """Configure the accepted trade dialog with explicit values."""
    ScriptedTradeDialog.expected_parent = window
    ScriptedTradeDialog.expected_type = transaction_type
    ScriptedTradeDialog.expected_assets = assets
    ScriptedTradeDialog.selected_asset_id = asset_id
    ScriptedTradeDialog.entered_quantity = Decimal(quantity)
    ScriptedTradeDialog.entered_price = Decimal(price)
    ScriptedTradeDialog.entered_at = occurred_at
    ScriptedTradeDialog.entered_commission = Decimal(commission)
    ScriptedTradeDialog.entered_tax = Decimal(tax)


def capture_critical_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[QMainWindow, str, str]]:
    """Capture critical dialogs without opening nested event loops."""
    errors: list[tuple[QMainWindow, str, str]] = []

    def record(parent: QMainWindow, title: str, message: str) -> QMessageBox.StandardButton:
        errors.append((parent, title, message))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "critical", record)
    return errors


def confirm_deletion(*_args: object) -> QMessageBox.StandardButton:
    """Accept a transaction deletion confirmation."""
    return QMessageBox.StandardButton.Yes


def cancel_deletion(*_args: object) -> QMessageBox.StandardButton:
    """Reject a transaction deletion confirmation."""
    return QMessageBox.StandardButton.No


def test_complete_buy_sell_delete_and_restart_presentation_workflow(
    monkeypatch: pytest.MonkeyPatch,
    open_desktop_window: DesktopWindowFactory,
) -> None:
    """Exercise the complete persisted-style Desktop Alpha acceptance path."""
    errors = capture_critical_errors(monkeypatch)
    harness = DesktopAlphaHarness()
    window = open_desktop_window(harness)

    portfolio_empty = child(window, QLabel, "portfolioEmptyState")
    asset_empty = child(window, QLabel, "assetEmptyState")
    transaction_table = child(window, QTableWidget, "transactionTable")
    position_table = child(window, QTableWidget, "positionTable")
    currency_table = child(window, QTableWidget, "currencyValuationTable")
    valuation_state = child(window, QLabel, "valuationState")
    buy_button = child(window, QPushButton, "buyAssetButton")
    sell_button = child(window, QPushButton, "sellAssetButton")
    delete_button = child(window, QPushButton, "deleteTransactionButton")
    price_button = child(window, QPushButton, "recordPriceButton")
    refresh_button = child(window, QPushButton, "refreshValuationButton")

    assert window._selected_portfolio_id is None
    assert "Create a portfolio" in portfolio_empty.text()
    assert portfolio_empty.isVisibleTo(window)
    assert asset_empty.text() == "No assets have been created yet."
    assert asset_empty.isVisibleTo(window)
    assert transaction_table.rowCount() == 0
    assert position_table.rowCount() == 0
    assert currency_table.rowCount() == 0
    assert valuation_state.text() == "Create or select a portfolio to view valuation."
    assert not buy_button.isEnabled()
    assert not sell_button.isEnabled()
    assert not delete_button.isEnabled()
    assert not price_button.isEnabled()
    assert not refresh_button.isEnabled()
    assert harness.dashboard_calls == []
    initial_text = " ".join(label.text() for label in window.findChildren(QLabel))
    assert "FLOW" not in initial_text
    assert harness.list_portfolio_calls == [ListPortfoliosQuery()]
    assert harness.list_asset_calls == [ListAssetsQuery()]

    AcceptedPortfolioDialog.expected_parent = window
    monkeypatch.setattr(
        main_window_module,
        "CreatePortfolioDialog",
        AcceptedPortfolioDialog,
    )
    window._create_portfolio()

    assert harness.create_portfolio_calls == [CreatePortfolioCommand(portfolio_name="Alpha")]
    assert harness.get_portfolio_calls == [GetPortfolioQuery(portfolio_id=PORTFOLIO_ID)]
    assert harness.dashboard_calls == [GetPortfolioDashboardQuery(portfolio_id=PORTFOLIO_ID)]
    assert window._selected_portfolio_id == PORTFOLIO_ID
    assert refresh_button.isEnabled()
    assert valuation_state.text() == (
        "No calculated positions yet. Record a BUY transaction to begin."
    )

    AcceptedAssetDialog.expected_parent = window
    monkeypatch.setattr(main_window_module, "CreateAssetDialog", AcceptedAssetDialog)
    dashboard_count = len(harness.dashboard_calls)
    window._create_asset()

    assert harness.create_asset_calls == [
        CreateAssetCommand(
            symbol="FLOW",
            name="Workflow Asset",
            asset_type=AssetType.EQUITY,
            currency=Currency.TRY,
        )
    ]
    assert len(harness.list_asset_calls) == 2
    assert len(harness.dashboard_calls) == dashboard_count
    asset_table = child(window, QTableWidget, "assetRegistryTable")
    assert asset_table.rowCount() == 1
    assert cell(asset_table, 0, 0).data(Qt.ItemDataRole.UserRole) == str(ASSET_ID)
    assert buy_button.isEnabled()
    assert price_button.isEnabled()

    configure_trade(
        window,
        TransactionType.BUY,
        (ASSET,),
        asset_id=ASSET_ID,
        quantity="10.000000000000000001",
        price="100.123400000000000001",
        occurred_at=BUY_AT,
        commission="1.2300",
        tax="0.0400",
    )
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", ScriptedTradeDialog)
    get_count = len(harness.get_portfolio_calls)
    dashboard_count = len(harness.dashboard_calls)
    window._buy_asset()

    assert harness.buy_calls == [
        BuyAssetCommand(
            portfolio_id=PORTFOLIO_ID,
            asset_id=ASSET_ID,
            quantity=Decimal("10.000000000000000001"),
            unit_price=Decimal("100.123400000000000001"),
            trade_datetime=BUY_AT,
            commission=Decimal("1.2300"),
            tax=Decimal("0.0400"),
        )
    ]
    assert len(harness.get_portfolio_calls) == get_count + 1
    assert len(harness.dashboard_calls) == dashboard_count + 1
    assert transaction_table.rowCount() == 1
    assert cell(transaction_table, 0, 0).data(Qt.ItemDataRole.UserRole) == str(BUY_ID)
    assert position_table.rowCount() == 0
    assert currency_table.rowCount() == 0
    assert valuation_state.text() == ("Valuation unavailable. Record a current price for FLOW.")
    assert "valuation unavailable" in window.statusBar().currentMessage().lower()
    assert errors == []

    ScriptedPriceDialog.expected_parent = window
    ScriptedPriceDialog.expected_assets = (ASSET,)
    ScriptedPriceDialog.selected_asset_id = ASSET_ID
    ScriptedPriceDialog.entered_price = Decimal("123.456700")
    ScriptedPriceDialog.entered_at = PRICE_AT
    monkeypatch.setattr(
        main_window_module,
        "RecordMarketPriceDialog",
        ScriptedPriceDialog,
    )
    get_count = len(harness.get_portfolio_calls)
    dashboard_count = len(harness.dashboard_calls)
    portfolio_list_count = len(harness.list_portfolio_calls)
    asset_list_count = len(harness.list_asset_calls)
    window._record_market_price()

    assert harness.record_price_calls == [
        RecordMarketPriceCommand(
            asset_id=ASSET_ID,
            price=Decimal("123.456700"),
            observed_at=PRICE_AT,
        )
    ]
    assert len(harness.get_portfolio_calls) == get_count
    assert len(harness.dashboard_calls) == dashboard_count + 1
    assert len(harness.list_portfolio_calls) == portfolio_list_count
    assert len(harness.list_asset_calls) == asset_list_count
    assert harness.latest_price_calls == []
    assert position_table.rowCount() == 1
    assert [cell(position_table, 0, column).text() for column in range(4, 13)] == [
        "10.000000000000000001",
        "100.123400000000000001",
        "1001.234000000000000010",
        "123.456700",
        "1234.567000000000000123",
        "0.1250",
        "233.333000000000000113",
        "999.999000000000000777",
        "2026-07-20 11:45:15 UTC",
    ]
    assert [cell(currency_table, 0, column).text() for column in range(6)] == [
        "TRY",
        "1001.234000000000000010",
        "1234.567000000000000123",
        "0.1250",
        "233.333000000000000113",
        "999.999000000000000777",
    ]

    calls_before_refresh = (
        len(harness.list_portfolio_calls),
        len(harness.list_asset_calls),
        len(harness.get_portfolio_calls),
        len(harness.create_portfolio_calls),
        len(harness.create_asset_calls),
        len(harness.buy_calls),
        len(harness.sell_calls),
        len(harness.delete_calls),
        len(harness.record_price_calls),
        len(harness.latest_price_calls),
        len(harness.dashboard_calls),
    )
    refresh_button.click()
    assert (
        len(harness.list_portfolio_calls),
        len(harness.list_asset_calls),
        len(harness.get_portfolio_calls),
        len(harness.create_portfolio_calls),
        len(harness.create_asset_calls),
        len(harness.buy_calls),
        len(harness.sell_calls),
        len(harness.delete_calls),
        len(harness.record_price_calls),
        len(harness.latest_price_calls),
        len(harness.dashboard_calls),
    ) == (*calls_before_refresh[:-1], calls_before_refresh[-1] + 1)
    assert window.statusBar().currentMessage() == "Valuation refreshed"

    configure_trade(
        window,
        TransactionType.SELL,
        (POSITION_ASSET,),
        asset_id=ASSET_ID,
        quantity="3.250000000000000000",
        price="130.567800000000000001",
        occurred_at=SELL_AT,
        commission="1.1100",
        tax="0.2200",
    )
    get_count = len(harness.get_portfolio_calls)
    dashboard_count = len(harness.dashboard_calls)
    window._sell_asset()

    assert harness.sell_calls == [
        SellAssetCommand(
            portfolio_id=PORTFOLIO_ID,
            asset_id=ASSET_ID,
            quantity=Decimal("3.250000000000000000"),
            unit_price=Decimal("130.567800000000000001"),
            trade_datetime=SELL_AT,
            commission=Decimal("1.1100"),
            tax=Decimal("0.2200"),
        )
    ]
    assert len(harness.get_portfolio_calls) == get_count + 1
    assert len(harness.dashboard_calls) == dashboard_count + 1
    assert transaction_table.rowCount() == 2
    assert cell(transaction_table, 1, 0).data(Qt.ItemDataRole.UserRole) == str(SELL_ID)
    assert [cell(position_table, 0, column).text() for column in range(4, 12)] == [
        "6.750000000000000001",
        "100.123400000000000001",
        "675.832950000000000100",
        "130.0000",
        "877.500000000000000130",
        "77.777700000000000007",
        "201.667050000000000030",
        "314.159265358979323846",
    ]

    transaction_table.selectRow(1)
    monkeypatch.setattr(QMessageBox, "question", confirm_deletion)
    get_count = len(harness.get_portfolio_calls)
    dashboard_count = len(harness.dashboard_calls)
    window._delete_selected_transaction()

    assert harness.delete_calls == [
        DeleteTransactionCommand(
            portfolio_id=PORTFOLIO_ID,
            transaction_id=SELL_ID,
        )
    ]
    assert len(harness.get_portfolio_calls) == get_count
    assert len(harness.dashboard_calls) == dashboard_count + 1
    assert window._selected_portfolio_details is AFTER_BUY_DETAILS
    assert transaction_table.rowCount() == 1
    assert cell(transaction_table, 0, 0).data(Qt.ItemDataRole.UserRole) == str(BUY_ID)
    assert cell(position_table, 0, 11).text() == "999.999000000000000777"

    write_counts = (
        len(harness.create_portfolio_calls),
        len(harness.create_asset_calls),
        len(harness.buy_calls),
        len(harness.sell_calls),
        len(harness.delete_calls),
        len(harness.record_price_calls),
    )
    portfolio_list_count = len(harness.list_portfolio_calls)
    asset_list_count = len(harness.list_asset_calls)
    get_count = len(harness.get_portfolio_calls)
    dashboard_count = len(harness.dashboard_calls)
    window.close()
    restored = open_desktop_window(harness)

    assert len(harness.list_portfolio_calls) == portfolio_list_count + 1
    assert len(harness.list_asset_calls) == asset_list_count + 1
    assert len(harness.get_portfolio_calls) == get_count + 1
    assert len(harness.dashboard_calls) == dashboard_count + 1
    assert (
        len(harness.create_portfolio_calls),
        len(harness.create_asset_calls),
        len(harness.buy_calls),
        len(harness.sell_calls),
        len(harness.delete_calls),
        len(harness.record_price_calls),
    ) == write_counts
    assert restored._selected_portfolio_id == PORTFOLIO_ID
    restored_asset_table = child(restored, QTableWidget, "assetRegistryTable")
    restored_transaction_table = child(restored, QTableWidget, "transactionTable")
    restored_position_table = child(restored, QTableWidget, "positionTable")
    restored_currency_table = child(restored, QTableWidget, "currencyValuationTable")
    assert restored_asset_table.rowCount() == 1
    assert cell(restored_asset_table, 0, 0).data(Qt.ItemDataRole.UserRole) == str(ASSET_ID)
    assert restored_transaction_table.rowCount() == 1
    assert cell(restored_transaction_table, 0, 0).data(Qt.ItemDataRole.UserRole) == str(BUY_ID)
    assert restored_position_table.rowCount() == 1
    assert cell(restored_position_table, 0, 0).data(Qt.ItemDataRole.UserRole) == str(ASSET_ID)
    assert cell(restored_position_table, 0, 11).text() == "999.999000000000000777"
    assert restored_currency_table.rowCount() == 1
    assert errors == []


def test_closed_reopened_multicurrency_and_duplicate_symbol_presentation(
    monkeypatch: pytest.MonkeyPatch,
    open_desktop_window: DesktopWindowFactory,
) -> None:
    """Keep DTO ordering and UUID identity across dense presentation edge cases."""
    first_id = UUID("50000000-0000-0000-0000-000000000001")
    second_id = UUID("50000000-0000-0000-0000-000000000002")
    first = AssetView(
        id=first_id,
        symbol="DUP",
        name="Closed then reopened",
        asset_type=AssetType.EQUITY,
        currency=Currency.USD,
        is_active=True,
        created_at=CREATED_AT,
    )
    second = AssetView(
        id=second_id,
        symbol="DUP",
        name="Independent duplicate",
        asset_type=AssetType.ETF,
        currency=Currency.TRY,
        is_active=True,
        created_at=CREATED_AT,
    )
    position_assets = tuple(
        AssetPositionView(
            id=asset.id,
            symbol=asset.symbol,
            name=asset.name,
            asset_type=asset.asset_type,
            currency=asset.currency,
            is_active=asset.is_active,
            created_at=asset.created_at,
        )
        for asset in (first, second)
    )
    summary = PortfolioSummary(
        id=PORTFOLIO_ID,
        name="Duplicate symbols",
        base_currency=Currency.TRY,
        is_archived=False,
        created_at=CREATED_AT,
    )
    initial_details = PortfolioDetails(
        id=summary.id,
        name=summary.name,
        base_currency=summary.base_currency,
        assets=position_assets,
        transactions=(),
        is_archived=False,
        created_at=CREATED_AT,
    )
    closed = valued_position(
        first,
        quantity="0",
        average_cost="0",
        cost_basis="0",
        market_price=None,
        price_observed_at=None,
        market_value="0",
        realized_pnl="45.678900000000000001",
        unrealized_pnl="0",
        total_pnl="45.678900000000000001",
    )
    independent = valued_position(
        second,
        quantity="2.0000",
        average_cost="10.0000",
        cost_basis="20.0000",
        market_price="25.5000",
        price_observed_at=PRICE_AT,
        market_value="51.0000",
        realized_pnl="-1.2500",
        unrealized_pnl="31.0000",
        total_pnl="29.7500",
    )
    usd = currency_valuation(
        Currency.USD,
        cost_basis="0",
        market_value="0",
        realized_pnl="45.678900000000000001",
        unrealized_pnl="0",
        total_pnl="45.678900000000000001",
    )
    try_value = currency_valuation(
        Currency.TRY,
        cost_basis="20.0000",
        market_value="51.0000",
        realized_pnl="-1.2500",
        unrealized_pnl="31.0000",
        total_pnl="29.7500",
    )
    initial_dashboard = PortfolioDashboard(
        portfolio=initial_details,
        positions=(closed, independent),
        currencies=(usd, try_value),
    )
    harness = DesktopAlphaHarness(
        portfolios=(summary,),
        details=initial_details,
        assets=(first, second),
        dashboard=initial_dashboard,
    )
    window = open_desktop_window(harness)

    assert harness.get_portfolio_calls == [GetPortfolioQuery(portfolio_id=PORTFOLIO_ID)]
    assert harness.dashboard_calls == [GetPortfolioDashboardQuery(portfolio_id=PORTFOLIO_ID)]
    asset_table = child(window, QTableWidget, "assetRegistryTable")
    position_table = child(window, QTableWidget, "positionTable")
    currency_table = child(window, QTableWidget, "currencyValuationTable")
    assert asset_table.rowCount() == 2
    assert [cell(asset_table, row, 0).text() for row in range(2)] == ["DUP", "DUP"]
    assert [cell(asset_table, row, 0).data(Qt.ItemDataRole.UserRole) for row in range(2)] == [
        str(first_id),
        str(second_id),
    ]
    assert position_table.rowCount() == 2
    assert [cell(position_table, row, 0).data(Qt.ItemDataRole.UserRole) for row in range(2)] == [
        str(first_id),
        str(second_id),
    ]
    assert cell(position_table, 0, 3).text() == "Closed"
    assert cell(position_table, 0, 7).text() == EM_DASH
    assert cell(position_table, 0, 9).text() == "45.678900000000000001"
    assert cell(position_table, 0, 11).text() == "45.678900000000000001"
    assert cell(position_table, 0, 12).text() == EM_DASH
    assert currency_table.rowCount() == 2
    assert [cell(currency_table, row, 0).text() for row in range(2)] == ["USD", "TRY"]
    assert [cell(currency_table, 0, column).text() for column in range(1, 6)] == [
        "0",
        "0",
        "45.678900000000000001",
        "0",
        "45.678900000000000001",
    ]
    assert [cell(currency_table, 1, column).text() for column in range(1, 6)] == [
        "20.0000",
        "51.0000",
        "-1.2500",
        "31.0000",
        "29.7500",
    ]
    assert child(window, QLabel, "noFxNotice").text() == (
        "Values are shown separately by currency. No FX conversion is applied."
    )
    visible_text = " ".join(label.text() for label in window.findChildren(QLabel))
    assert "Total Portfolio Value" not in visible_text

    trade_dialog = RecordTradeDialog(TransactionType.BUY, harness.assets, window)
    trade_asset_selector = child(trade_dialog, QComboBox, "tradeAssetInput")
    assert trade_asset_selector.count() == 2
    assert [trade_asset_selector.itemText(index) for index in range(2)] == [
        f"DUP {EM_DASH} Closed then reopened (USD)",
        f"DUP {EM_DASH} Independent duplicate (TRY)",
    ]
    assert [trade_asset_selector.itemData(index) for index in range(2)] == [
        str(first_id),
        str(second_id),
    ]
    trade_dialog.close()
    trade_dialog.deleteLater()

    duplicate_buy = TransactionView(
        id=UUID("60000000-0000-0000-0000-000000000001"),
        asset_id=second_id,
        quantity=Decimal("1.5000"),
        price=Decimal("20.0000"),
        transaction_type=TransactionType.BUY,
        commission=Decimal("0"),
        tax=Decimal("0"),
        date=BUY_AT,
    )
    after_duplicate_buy = PortfolioDetails(
        id=initial_details.id,
        name=initial_details.name,
        base_currency=initial_details.base_currency,
        assets=initial_details.assets,
        transactions=(duplicate_buy,),
        is_archived=initial_details.is_archived,
        created_at=initial_details.created_at,
    )
    dashboard_after_duplicate_buy = PortfolioDashboard(
        portfolio=after_duplicate_buy,
        positions=initial_dashboard.positions,
        currencies=initial_dashboard.currencies,
    )
    harness.buy_result = duplicate_buy
    harness.details_after_buy = after_duplicate_buy
    harness.dashboard_after_buy = dashboard_after_duplicate_buy
    configure_trade(
        window,
        TransactionType.BUY,
        (first, second),
        asset_id=second_id,
        quantity="1.5000",
        price="20.0000",
        occurred_at=BUY_AT,
        commission="0",
        tax="0",
    )
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", ScriptedTradeDialog)
    window._buy_asset()

    assert harness.buy_calls[-1] == BuyAssetCommand(
        portfolio_id=PORTFOLIO_ID,
        asset_id=second_id,
        quantity=Decimal("1.5000"),
        unit_price=Decimal("20.0000"),
        trade_datetime=BUY_AT,
        commission=Decimal("0"),
        tax=Decimal("0"),
    )
    assert position_table.rowCount() == 2
    assert [cell(position_table, row, 0).data(Qt.ItemDataRole.UserRole) for row in range(2)] == [
        str(first_id),
        str(second_id),
    ]

    reopened = valued_position(
        first,
        quantity="4.125000000000000001",
        average_cost="12.345600000000000001",
        cost_basis="50.925600000000000016",
        market_price="19.876500",
        price_observed_at=SELL_AT,
        market_value="81.988062500000000020",
        realized_pnl="45.678900000000000001",
        unrealized_pnl="31.062462500000000004",
        total_pnl="123.456789012345678901",
    )
    reopened_dashboard = PortfolioDashboard(
        portfolio=after_duplicate_buy,
        positions=(reopened, independent),
        currencies=(usd, try_value),
    )
    harness.dashboard_result = reopened_dashboard
    dashboard_count = len(harness.dashboard_calls)
    child(window, QPushButton, "refreshValuationButton").click()

    assert len(harness.dashboard_calls) == dashboard_count + 1
    assert cell(position_table, 0, 3).text() == "Open"
    assert cell(position_table, 0, 7).text() == "19.876500"
    assert cell(position_table, 0, 9).text() == "45.678900000000000001"
    assert cell(position_table, 0, 10).text() == "31.062462500000000004"
    assert cell(position_table, 0, 11).text() == "123.456789012345678901"
    assert cell(position_table, 0, 12).text() == "2026-07-21 14:05:45 UTC"


def test_cancellation_and_failure_paths_are_atomic(
    monkeypatch: pytest.MonkeyPatch,
    open_desktop_window: DesktopWindowFactory,
) -> None:
    """Reject unconfirmed writes while clearing only a failed valuation query."""
    errors = capture_critical_errors(monkeypatch)
    harness = DesktopAlphaHarness(
        portfolios=(PORTFOLIO_SUMMARY,),
        details=AFTER_BUY_DETAILS,
        assets=(ASSET,),
        dashboard=BUY_DASHBOARD,
    )
    window = open_desktop_window(harness)
    transaction_table = child(window, QTableWidget, "transactionTable")
    position_table = child(window, QTableWidget, "positionTable")
    currency_table = child(window, QTableWidget, "currencyValuationTable")
    asset_table = child(window, QTableWidget, "assetRegistryTable")
    confirmed_dashboard = window._portfolio_dashboard
    initial_status = window.statusBar().currentMessage()

    RejectedTradeDialog.expected_parent = window
    RejectedTradeDialog.expected_type = TransactionType.BUY
    RejectedTradeDialog.expected_assets = (ASSET,)
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", RejectedTradeDialog)
    dashboard_count = len(harness.dashboard_calls)
    get_count = len(harness.get_portfolio_calls)
    window._buy_asset()

    assert harness.buy_calls == []
    assert len(harness.dashboard_calls) == dashboard_count
    assert len(harness.get_portfolio_calls) == get_count
    assert window._portfolio_dashboard is confirmed_dashboard
    assert transaction_table.rowCount() == 1
    assert position_table.rowCount() == 1
    assert window.statusBar().currentMessage() == initial_status

    configure_trade(
        window,
        TransactionType.SELL,
        (POSITION_ASSET,),
        asset_id=ASSET_ID,
        quantity="1.0000",
        price="130.0000",
        occurred_at=SELL_AT,
        commission="0",
        tax="0",
    )
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", ScriptedTradeDialog)
    harness.sell_error = ValidationError("SELL rejected by fake persistence.")
    dashboard_count = len(harness.dashboard_calls)
    get_count = len(harness.get_portfolio_calls)
    window._sell_asset()

    assert len(harness.sell_calls) == 1
    assert len(harness.dashboard_calls) == dashboard_count
    assert len(harness.get_portfolio_calls) == get_count
    assert window._portfolio_dashboard is confirmed_dashboard
    assert transaction_table.rowCount() == 1
    assert position_table.rowCount() == 1
    assert window.statusBar().currentMessage() == "Unable to record transaction"
    assert errors[-1][1] == "Unable to record transaction"

    RejectedPriceDialog.expected_parent = window
    RejectedPriceDialog.expected_assets = (ASSET,)
    monkeypatch.setattr(
        main_window_module,
        "RecordMarketPriceDialog",
        RejectedPriceDialog,
    )
    dashboard_count = len(harness.dashboard_calls)
    window._record_market_price()

    assert harness.record_price_calls == []
    assert len(harness.dashboard_calls) == dashboard_count
    assert window._portfolio_dashboard is confirmed_dashboard
    assert transaction_table.rowCount() == 1
    assert position_table.rowCount() == 1
    assert window.statusBar().currentMessage() == "Unable to record transaction"

    harness.dashboard_result = ValidationError("Dashboard refresh failed.")
    list_counts = (len(harness.list_portfolio_calls), len(harness.list_asset_calls))
    get_count = len(harness.get_portfolio_calls)
    write_counts = (
        len(harness.buy_calls),
        len(harness.sell_calls),
        len(harness.delete_calls),
        len(harness.record_price_calls),
    )
    dashboard_count = len(harness.dashboard_calls)
    child(window, QPushButton, "refreshValuationButton").click()

    assert len(harness.dashboard_calls) == dashboard_count + 1
    assert (len(harness.list_portfolio_calls), len(harness.list_asset_calls)) == list_counts
    assert len(harness.get_portfolio_calls) == get_count
    assert (
        len(harness.buy_calls),
        len(harness.sell_calls),
        len(harness.delete_calls),
        len(harness.record_price_calls),
    ) == write_counts
    assert window._portfolio_dashboard is None
    assert position_table.rowCount() == 0
    assert currency_table.rowCount() == 0
    assert transaction_table.rowCount() == 1
    assert asset_table.rowCount() == 1
    assert errors[-1][1] == "Unable to calculate valuation"
    assert window.statusBar().currentMessage() == "Unable to calculate valuation"

    transaction_table.selectRow(0)
    monkeypatch.setattr(QMessageBox, "question", cancel_deletion)
    dashboard_count = len(harness.dashboard_calls)
    window._delete_selected_transaction()

    assert harness.delete_calls == []
    assert len(harness.dashboard_calls) == dashboard_count
    assert transaction_table.rowCount() == 1
    assert cell(transaction_table, 0, 0).data(Qt.ItemDataRole.UserRole) == str(BUY_ID)
    assert window.statusBar().currentMessage() == "Unable to calculate valuation"
    assert harness.latest_price_calls == []


def test_desktop_alpha_production_boundaries_remain_presentation_only() -> None:
    """Keep the accepted Desktop Alpha UI on application DTO/service boundaries."""
    project_root = Path(__file__).resolve().parents[3]
    source = "\n".join(
        (
            (project_root / "app/ui/windows/main_window.py").read_text(encoding="utf-8"),
            (project_root / "app/ui/components/portfolio_valuation_panel.py").read_text(
                encoding="utf-8"
            ),
        )
    )

    for forbidden in (
        "app.infrastructure",
        "sqlalchemy",
        "container.unit_of_work_factory",
        "container.session_factory",
        "container.database_manager",
        "UnitOfWork",
        "DatabaseManager",
        "GetLatestMarketPriceQuery",
        "get_latest_market_price",
        "PriceHistoryRepository",
        "PortfolioPositionCalculator",
        "PortfolioValuationCalculator",
        "float(",
        "round(",
        "quantize(",
        "quantity *",
        "realized_pnl +",
        "sum(",
        "grand_total",
        "portfolio_total",
        "total_market_value",
        "base_currency_value",
        "fx_rate",
        "QTimer",
        "QThread",
        "threading",
        "asyncio",
        "async def",
    ):
        assert forbidden not in source
