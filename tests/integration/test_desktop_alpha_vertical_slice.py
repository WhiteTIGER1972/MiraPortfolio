"""Real-service integration coverage for the Desktop Alpha vertical slice."""

import os
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
)

from app.application.commands import (
    BuyAssetCommand,
    CreateAssetCommand,
    CreatePortfolioCommand,
    RecordMarketPriceCommand,
    SellAssetCommand,
)
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
    ValuedAssetPositionView,
)
from app.application.services import (
    DefaultAssetApplicationService,
    DefaultMarketPriceApplicationService,
    DefaultPortfolioApplicationService,
    DefaultPortfolioDashboardQueryService,
)
from app.core.container import Container, build_container
from app.core.exceptions import DatabaseError
from app.core.settings import Settings
from app.domain.entities.asset import AssetType
from app.domain.entities.transaction import TransactionType
from app.domain.value_objects.currency import Currency
from app.infrastructure.database import DatabaseManager
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SQLAlchemyUnitOfWork
from app.ui.windows import main_window as main_window_module
from app.ui.windows.main_window import MainWindow

BUY_AT = datetime(2026, 7, 22, 9, 15, 10, tzinfo=UTC)
PRICE_AT = datetime(2026, 7, 22, 10, 30, 20, tzinfo=UTC)
SELL_AT = datetime(2026, 7, 23, 11, 45, 30, tzinfo=UTC)
REOPEN_AT = datetime(2026, 7, 24, 13, 5, 40, tzinfo=UTC)
REOPEN_PRICE_AT = datetime(2026, 7, 24, 14, 10, 50, tzinfo=UTC)
EM_DASH = "\u2014"


@pytest.fixture(scope="session")
def qapplication() -> Iterator[QApplication]:
    """Provide a process-wide offscreen QApplication for integration tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    existing = QApplication.instance()
    if existing is None:
        application = QApplication([])
    elif isinstance(existing, QApplication):
        application = existing
    else:
        raise RuntimeError("A non-GUI Qt application instance already exists.")
    yield application
    application.processEvents()


def make_settings(tmp_path: Path, name: str) -> tuple[Settings, Path]:
    """Create settings whose every filesystem path remains under tmp_path."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    database = root / "portfolio.db"
    return (
        Settings(
            app_name=f"Mira Vertical Slice {name}",
            environment="test",
            auto_backup=False,
            auto_snapshot=False,
            database_url=f"sqlite:///{database.as_posix()}",
            cache_directory=root / "cache",
            database_directory=root / "data",
            export_directory=root / "exports",
            backup_directory=root / "backups",
        ),
        database,
    )


def assert_real_service_graph(container: Container) -> None:
    """Confirm the Container uses every accepted default implementation."""
    assert type(container.portfolio_application_service) is DefaultPortfolioApplicationService
    assert type(container.asset_application_service) is DefaultAssetApplicationService
    assert type(container.market_price_application_service) is (
        DefaultMarketPriceApplicationService
    )
    assert type(container.portfolio_dashboard_query_service) is (
        DefaultPortfolioDashboardQueryService
    )
    first_unit = container.unit_of_work_factory()
    second_unit = container.unit_of_work_factory()
    assert isinstance(first_unit, SQLAlchemyUnitOfWork)
    assert isinstance(second_unit, SQLAlchemyUnitOfWork)
    assert first_unit is not second_unit


def child[WidgetT: QObject](
    parent: QObject,
    widget_type: type[WidgetT],
    object_name: str,
) -> WidgetT:
    """Return one required named Qt child."""
    widget = parent.findChild(widget_type, object_name)
    assert widget is not None
    return widget


def cell(table: QTableWidget, row: int, column: int) -> QTableWidgetItem:
    """Return one populated table item."""
    item = table.item(row, column)
    assert item is not None
    return item


def close_window(window: MainWindow | None, application: QApplication) -> None:
    """Close one MainWindow and process its deferred Qt cleanup."""
    if window is None:
        return
    window.close()
    window.deleteLater()
    application.processEvents()


def format_optional_decimal(value: Decimal | None) -> str:
    """Format one DTO Decimal exactly as the production presentation policy."""
    return EM_DASH if value is None else format(value, "f")


def assert_position_row(
    table: QTableWidget,
    row: int,
    position: ValuedAssetPositionView,
) -> None:
    """Compare a position row directly with its real application DTO."""
    expected_timestamp = (
        EM_DASH
        if position.price_observed_at is None
        else f"{position.price_observed_at.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )
    assert cell(table, row, 0).data(Qt.ItemDataRole.UserRole) == str(position.asset_id)
    assert cell(table, row, 3).text() == ("Closed" if position.quantity == Decimal("0") else "Open")
    assert [cell(table, row, column).text() for column in range(4, 13)] == [
        format(position.quantity, "f"),
        format(position.average_cost, "f"),
        format(position.cost_basis, "f"),
        format_optional_decimal(position.market_price),
        format(position.market_value, "f"),
        format(position.realized_pnl, "f"),
        format(position.unrealized_pnl, "f"),
        format(position.total_pnl, "f"),
        expected_timestamp,
    ]


def assert_currency_row(
    table: QTableWidget,
    row: int,
    valuation: CurrencyValuationView,
) -> None:
    """Compare a currency row directly with its real application DTO."""
    assert [cell(table, row, column).text() for column in range(6)] == [
        valuation.currency.value,
        format(valuation.cost_basis, "f"),
        format(valuation.market_value, "f"),
        format(valuation.realized_pnl, "f"),
        format(valuation.unrealized_pnl, "f"),
        format(valuation.total_pnl, "f"),
    ]


def capture_critical_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[QMainWindow, str, str]]:
    """Capture modal critical errors without entering a nested event loop."""
    errors: list[tuple[QMainWindow, str, str]] = []

    def record(parent: QMainWindow, title: str, message: str) -> QMessageBox.StandardButton:
        errors.append((parent, title, message))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "critical", record)
    return errors


def confirm_deletion(*_args: object) -> QMessageBox.StandardButton:
    """Accept the scripted deletion confirmation."""
    return QMessageBox.StandardButton.Yes


class ScriptedPortfolioDialog:
    """Return one accepted Portfolio name."""

    expected_parent: ClassVar[MainWindow | None] = None
    entered_name: ClassVar[str] = ""

    def __init__(self, parent: MainWindow) -> None:
        assert parent is self.expected_parent

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def portfolio_name(self) -> str:
        return self.entered_name


class ScriptedAssetDialog:
    """Return one accepted Asset command payload."""

    expected_parent: ClassVar[MainWindow | None] = None
    entered_symbol: ClassVar[str] = ""
    entered_name: ClassVar[str] = ""
    entered_type: ClassVar[AssetType] = AssetType.EQUITY
    entered_currency: ClassVar[Currency] = Currency.TRY

    def __init__(self, parent: MainWindow) -> None:
        assert parent is self.expected_parent

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def symbol(self) -> str:
        return self.entered_symbol

    def asset_name(self) -> str:
        return self.entered_name

    def asset_type(self) -> AssetType:
        return self.entered_type

    def currency(self) -> Currency:
        return self.entered_currency


class ScriptedTradeDialog:
    """Return one accepted BUY or SELL payload."""

    expected_parent: ClassVar[MainWindow | None] = None
    expected_type: ClassVar[TransactionType] = TransactionType.BUY
    expected_assets: ClassVar[tuple[AssetView | AssetPositionView, ...]] = ()
    selected_asset_id: ClassVar[UUID]
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


class ScriptedPriceDialog:
    """Return one accepted manual-price payload."""

    expected_parent: ClassVar[MainWindow | None] = None
    expected_assets: ClassVar[tuple[AssetView, ...]] = ()
    selected_asset_id: ClassVar[UUID]
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


def configure_trade(
    window: MainWindow,
    transaction_type: TransactionType,
    assets: tuple[AssetView | AssetPositionView, ...],
    *,
    asset_id: UUID,
    quantity: Decimal,
    unit_price: Decimal,
    occurred_at: datetime,
    commission: Decimal,
    tax: Decimal,
) -> None:
    """Configure the scripted trade dialog with typed exact values."""
    ScriptedTradeDialog.expected_parent = window
    ScriptedTradeDialog.expected_type = transaction_type
    ScriptedTradeDialog.expected_assets = assets
    ScriptedTradeDialog.selected_asset_id = asset_id
    ScriptedTradeDialog.entered_quantity = quantity
    ScriptedTradeDialog.entered_price = unit_price
    ScriptedTradeDialog.entered_at = occurred_at
    ScriptedTradeDialog.entered_commission = commission
    ScriptedTradeDialog.entered_tax = tax


def create_asset(
    container: Container,
    *,
    symbol: str,
    name: str,
    currency: Currency,
) -> AssetView:
    """Create an Asset through the real application service."""
    return container.asset_application_service.create_asset(
        CreateAssetCommand(
            symbol=symbol,
            name=name,
            asset_type=AssetType.EQUITY,
            currency=currency,
        )
    )


def buy_asset(
    container: Container,
    *,
    portfolio_id: UUID,
    asset_id: UUID,
    quantity: Decimal,
    unit_price: Decimal,
    occurred_at: datetime,
) -> UUID:
    """Record a BUY through the real application service."""
    return container.portfolio_application_service.buy_asset(
        BuyAssetCommand(
            portfolio_id=portfolio_id,
            asset_id=asset_id,
            quantity=quantity,
            unit_price=unit_price,
            trade_datetime=occurred_at,
        )
    ).id


def sell_asset(
    container: Container,
    *,
    portfolio_id: UUID,
    asset_id: UUID,
    quantity: Decimal,
    unit_price: Decimal,
    occurred_at: datetime,
) -> UUID:
    """Record a SELL through the real application service."""
    return container.portfolio_application_service.sell_asset(
        SellAssetCommand(
            portfolio_id=portfolio_id,
            asset_id=asset_id,
            quantity=quantity,
            unit_price=unit_price,
            trade_datetime=occurred_at,
        )
    ).id


def record_price(
    container: Container,
    *,
    asset_id: UUID,
    price: Decimal,
    observed_at: datetime,
) -> UUID:
    """Record a market price through the real application service."""
    return container.market_price_application_service.record_market_price(
        RecordMarketPriceCommand(
            asset_id=asset_id,
            price=price,
            observed_at=observed_at,
        )
    ).id


def test_main_window_real_services_persist_complete_workflow_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qapplication: QApplication,
) -> None:
    """Exercise MainWindow through the complete real persisted vertical slice."""
    settings, database_path = make_settings(tmp_path, "core")
    first_manager = DatabaseManager(settings).initialize()
    second_manager: DatabaseManager | None = None
    first_window: MainWindow | None = None
    second_window: MainWindow | None = None
    try:
        first_container = build_container(settings, first_manager)
        assert_real_service_graph(first_container)
        errors = capture_critical_errors(monkeypatch)
        first_window = MainWindow(first_container)

        assert database_path.exists()
        assert (
            first_container.portfolio_application_service.list_portfolios(ListPortfoliosQuery())
            == ()
        )
        assert first_container.asset_application_service.list_assets(ListAssetsQuery()) == ()
        assert first_window._selected_portfolio_id is None
        assert child(first_window, QTableWidget, "transactionTable").rowCount() == 0
        assert child(first_window, QTableWidget, "positionTable").rowCount() == 0
        assert child(first_window, QTableWidget, "currencyValuationTable").rowCount() == 0
        assert (
            "Create a portfolio"
            in child(
                first_window,
                QLabel,
                "portfolioEmptyState",
            ).text()
        )
        assert child(first_window, QLabel, "assetEmptyState").text() == (
            "No assets have been created yet."
        )

        ScriptedPortfolioDialog.expected_parent = first_window
        ScriptedPortfolioDialog.entered_name = "Desktop Alpha Vertical Slice"
        monkeypatch.setattr(
            main_window_module,
            "CreatePortfolioDialog",
            ScriptedPortfolioDialog,
        )
        first_window._create_portfolio()

        portfolio_id = first_window._selected_portfolio_id
        assert portfolio_id is not None
        summaries = first_container.portfolio_application_service.list_portfolios(
            ListPortfoliosQuery()
        )
        assert len(summaries) == 1
        assert summaries[0].id == portfolio_id
        empty_details = first_container.portfolio_application_service.get_portfolio(
            GetPortfolioQuery(portfolio_id=portfolio_id)
        )
        assert empty_details.id == portfolio_id
        assert empty_details.assets == ()
        assert empty_details.transactions == ()
        empty_dashboard = first_container.portfolio_dashboard_query_service.get_dashboard(
            GetPortfolioDashboardQuery(portfolio_id=portfolio_id)
        )
        assert empty_dashboard.positions == ()
        assert empty_dashboard.currencies == ()
        assert child(first_window, QLabel, "valuationState").text() == (
            "No calculated positions yet. Record a BUY transaction to begin."
        )

        ScriptedAssetDialog.expected_parent = first_window
        ScriptedAssetDialog.entered_symbol = "  alpha  "
        ScriptedAssetDialog.entered_name = "  Alpha Incorporated  "
        ScriptedAssetDialog.entered_type = AssetType.EQUITY
        ScriptedAssetDialog.entered_currency = Currency.TRY
        monkeypatch.setattr(
            main_window_module,
            "CreateAssetDialog",
            ScriptedAssetDialog,
        )
        first_window._create_asset()

        assets = first_container.asset_application_service.list_assets(ListAssetsQuery())
        assert len(assets) == 1
        asset = assets[0]
        asset_id = asset.id
        assert asset.symbol == "ALPHA"
        assert asset.name == "Alpha Incorporated"
        asset_table = child(first_window, QTableWidget, "assetRegistryTable")
        assert asset_table.rowCount() == 1
        assert cell(asset_table, 0, 0).text() == "ALPHA"
        assert cell(asset_table, 0, 1).text() == "Alpha Incorporated"
        assert cell(asset_table, 0, 0).data(Qt.ItemDataRole.UserRole) == str(asset_id)
        assert first_window._selected_portfolio_details == empty_details
        assert child(first_window, QTableWidget, "transactionTable").rowCount() == 0
        assert child(first_window, QTableWidget, "positionTable").rowCount() == 0

        buy_quantity = Decimal("10.250000000000000001")
        buy_price = Decimal("100.400000000000000001")
        buy_commission = Decimal("1.230000000000000001")
        buy_tax = Decimal("0.040000000000000001")
        configure_trade(
            first_window,
            TransactionType.BUY,
            (asset,),
            asset_id=asset_id,
            quantity=buy_quantity,
            unit_price=buy_price,
            occurred_at=BUY_AT,
            commission=buy_commission,
            tax=buy_tax,
        )
        monkeypatch.setattr(
            main_window_module,
            "RecordTradeDialog",
            ScriptedTradeDialog,
        )
        first_window._buy_asset()

        transaction_table = child(first_window, QTableWidget, "transactionTable")
        assert transaction_table.rowCount() == 1
        buy_id = UUID(str(cell(transaction_table, 0, 0).data(Qt.ItemDataRole.UserRole)))
        details_after_buy = first_container.portfolio_application_service.get_portfolio(
            GetPortfolioQuery(portfolio_id=portfolio_id)
        )
        assert [held.id for held in details_after_buy.assets] == [asset_id]
        assert len(details_after_buy.transactions) == 1
        persisted_buy = details_after_buy.transactions[0]
        assert persisted_buy.id == buy_id
        assert persisted_buy.asset_id == asset_id
        assert persisted_buy.quantity == buy_quantity
        assert persisted_buy.price == buy_price
        assert persisted_buy.commission == buy_commission
        assert persisted_buy.tax == buy_tax
        assert persisted_buy.date == BUY_AT
        assert [cell(transaction_table, 0, column).text() for column in range(3, 8)] == [
            format(buy_quantity, "f"),
            format(buy_price, "f"),
            "TRY",
            format(buy_commission, "f"),
            format(buy_tax, "f"),
        ]
        assert child(first_window, QLabel, "valuationState").text() == (
            "Valuation unavailable. Record a current price for ALPHA."
        )
        assert child(first_window, QTableWidget, "positionTable").rowCount() == 0
        assert child(first_window, QTableWidget, "currencyValuationTable").rowCount() == 0
        assert transaction_table.rowCount() == 1
        assert errors == []

        recorded_market_price = Decimal("140.123456789012345678")
        ScriptedPriceDialog.expected_parent = first_window
        ScriptedPriceDialog.expected_assets = (asset,)
        ScriptedPriceDialog.selected_asset_id = asset_id
        ScriptedPriceDialog.entered_price = recorded_market_price
        ScriptedPriceDialog.entered_at = PRICE_AT
        monkeypatch.setattr(
            main_window_module,
            "RecordMarketPriceDialog",
            ScriptedPriceDialog,
        )
        first_window._record_market_price()

        latest_price = first_container.market_price_application_service.get_latest_market_price(
            GetLatestMarketPriceQuery(asset_id=asset_id)
        )
        assert latest_price is not None
        price_id = latest_price.id
        assert latest_price.asset_id == asset_id
        assert latest_price.price == recorded_market_price
        assert latest_price.observed_at == PRICE_AT
        buy_dashboard = first_container.portfolio_dashboard_query_service.get_dashboard(
            GetPortfolioDashboardQuery(portfolio_id=portfolio_id)
        )
        assert len(buy_dashboard.positions) == 1
        assert len(buy_dashboard.currencies) == 1
        assert buy_dashboard.currencies[0].currency is Currency.TRY
        position_table = child(first_window, QTableWidget, "positionTable")
        currency_table = child(first_window, QTableWidget, "currencyValuationTable")
        assert_position_row(position_table, 0, buy_dashboard.positions[0])
        assert_currency_row(currency_table, 0, buy_dashboard.currencies[0])

        sell_quantity = Decimal("4.125000000000000001")
        sell_price = Decimal("130.200000000000000001")
        sell_commission = Decimal("0.330000000000000001")
        sell_tax = Decimal("0.070000000000000001")
        configure_trade(
            first_window,
            TransactionType.SELL,
            details_after_buy.assets,
            asset_id=asset_id,
            quantity=sell_quantity,
            unit_price=sell_price,
            occurred_at=SELL_AT,
            commission=sell_commission,
            tax=sell_tax,
        )
        first_window._sell_asset()

        assert transaction_table.rowCount() == 2
        sell_id = UUID(str(cell(transaction_table, 1, 0).data(Qt.ItemDataRole.UserRole)))
        details_after_sell = first_container.portfolio_application_service.get_portfolio(
            GetPortfolioQuery(portfolio_id=portfolio_id)
        )
        assert [item.id for item in details_after_sell.transactions] == [buy_id, sell_id]
        persisted_sell = details_after_sell.transactions[1]
        assert persisted_sell.asset_id == asset_id
        assert persisted_sell.quantity == sell_quantity
        assert persisted_sell.price == sell_price
        assert persisted_sell.commission == sell_commission
        assert persisted_sell.tax == sell_tax
        assert persisted_sell.date == SELL_AT
        sell_dashboard = first_container.portfolio_dashboard_query_service.get_dashboard(
            GetPortfolioDashboardQuery(portfolio_id=portfolio_id)
        )
        assert len(sell_dashboard.positions) == 1
        sell_position = sell_dashboard.positions[0]
        assert sell_position.quantity > Decimal("0")
        assert sell_position.realized_pnl != Decimal("0")
        assert sell_position.unrealized_pnl != Decimal("0")
        assert_position_row(position_table, 0, sell_position)
        assert_currency_row(currency_table, 0, sell_dashboard.currencies[0])

        transaction_table.selectRow(1)
        monkeypatch.setattr(QMessageBox, "question", confirm_deletion)
        real_get_portfolio = first_container.portfolio_application_service.get_portfolio
        deletion_get_calls: list[GetPortfolioQuery] = []

        def track_get_portfolio(query: GetPortfolioQuery) -> object:
            deletion_get_calls.append(query)
            return real_get_portfolio(query)

        monkeypatch.setattr(
            first_container.portfolio_application_service,
            "get_portfolio",
            track_get_portfolio,
        )
        first_window._delete_selected_transaction()

        assert deletion_get_calls == []
        assert transaction_table.rowCount() == 1
        assert UUID(str(cell(transaction_table, 0, 0).data(Qt.ItemDataRole.UserRole))) == buy_id
        assert first_window._selected_portfolio_details is not None
        assert [item.id for item in first_window._selected_portfolio_details.transactions] == [
            buy_id
        ]
        dashboard_after_delete = first_container.portfolio_dashboard_query_service.get_dashboard(
            GetPortfolioDashboardQuery(portfolio_id=portfolio_id)
        )
        assert dashboard_after_delete == buy_dashboard
        assert_position_row(position_table, 0, dashboard_after_delete.positions[0])
        latest_after_delete = (
            first_container.market_price_application_service.get_latest_market_price(
                GetLatestMarketPriceQuery(asset_id=asset_id)
            )
        )
        assert latest_after_delete is not None
        assert latest_after_delete.id == price_id
        assert latest_after_delete.price == recorded_market_price

        close_window(first_window, qapplication)
        first_window = None
        first_manager.shutdown()
        with pytest.raises(DatabaseError, match="has not been initialized"):
            _ = first_manager.engine

        second_manager = DatabaseManager(settings).initialize()
        second_container = build_container(settings, second_manager)
        assert_real_service_graph(second_container)
        second_window = MainWindow(second_container)

        restored_portfolios = second_container.portfolio_application_service.list_portfolios(
            ListPortfoliosQuery()
        )
        restored_assets = second_container.asset_application_service.list_assets(ListAssetsQuery())
        restored_details = second_container.portfolio_application_service.get_portfolio(
            GetPortfolioQuery(portfolio_id=portfolio_id)
        )
        restored_price = second_container.market_price_application_service.get_latest_market_price(
            GetLatestMarketPriceQuery(asset_id=asset_id)
        )
        restored_dashboard = second_container.portfolio_dashboard_query_service.get_dashboard(
            GetPortfolioDashboardQuery(portfolio_id=portfolio_id)
        )
        assert [item.id for item in restored_portfolios] == [portfolio_id]
        assert [item.id for item in restored_assets] == [asset_id]
        assert [item.id for item in restored_details.assets] == [asset_id]
        assert [item.id for item in restored_details.transactions] == [buy_id]
        assert restored_price is not None
        assert restored_price.id == price_id
        assert restored_price.price == recorded_market_price
        assert restored_dashboard == dashboard_after_delete
        assert second_window._selected_portfolio_id == portfolio_id
        assert child(second_window, QTableWidget, "assetRegistryTable").rowCount() == 1
        assert child(second_window, QTableWidget, "transactionTable").rowCount() == 1
        restored_position_table = child(second_window, QTableWidget, "positionTable")
        restored_currency_table = child(second_window, QTableWidget, "currencyValuationTable")
        assert_position_row(restored_position_table, 0, restored_dashboard.positions[0])
        assert_currency_row(restored_currency_table, 0, restored_dashboard.currencies[0])
        assert errors == []
    finally:
        close_window(second_window, qapplication)
        close_window(first_window, qapplication)
        if second_manager is not None:
            second_manager.shutdown()
        first_manager.shutdown()


def test_real_persistence_keeps_multiple_currencies_and_duplicate_symbols_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qapplication: QApplication,
) -> None:
    """Persist duplicate symbols by UUID and retain independent currency buckets."""
    settings, _ = make_settings(tmp_path, "duplicates")
    manager = DatabaseManager(settings).initialize()
    window: MainWindow | None = None
    try:
        container = build_container(settings, manager)
        errors = capture_critical_errors(monkeypatch)
        portfolio = container.portfolio_application_service.create_portfolio(
            CreatePortfolioCommand(portfolio_name="Multiple currencies")
        )
        first = create_asset(
            container,
            symbol="dup",
            name="TRY Duplicate",
            currency=Currency.TRY,
        )
        second = create_asset(
            container,
            symbol=" DUP ",
            name="USD Duplicate",
            currency=Currency.USD,
        )
        assert first.symbol == second.symbol == "DUP"
        assert first.id != second.id

        first_transaction_id = buy_asset(
            container,
            portfolio_id=portfolio.id,
            asset_id=first.id,
            quantity=Decimal("2.500000000000000001"),
            unit_price=Decimal("10.100000000000000001"),
            occurred_at=BUY_AT,
        )
        second_transaction_id = buy_asset(
            container,
            portfolio_id=portfolio.id,
            asset_id=second.id,
            quantity=Decimal("3.750000000000000001"),
            unit_price=Decimal("20.200000000000000001"),
            occurred_at=SELL_AT,
        )
        first_price_id = record_price(
            container,
            asset_id=first.id,
            price=Decimal("15.300000000000000001"),
            observed_at=PRICE_AT,
        )
        second_price_id = record_price(
            container,
            asset_id=second.id,
            price=Decimal("31.700000000000000001"),
            observed_at=REOPEN_PRICE_AT,
        )

        details = container.portfolio_application_service.get_portfolio(
            GetPortfolioQuery(portfolio_id=portfolio.id)
        )
        assert [item.asset_id for item in details.transactions] == [first.id, second.id]
        assert [item.id for item in details.transactions] == [
            first_transaction_id,
            second_transaction_id,
        ]
        first_latest = container.market_price_application_service.get_latest_market_price(
            GetLatestMarketPriceQuery(asset_id=first.id)
        )
        second_latest = container.market_price_application_service.get_latest_market_price(
            GetLatestMarketPriceQuery(asset_id=second.id)
        )
        assert first_latest is not None
        assert second_latest is not None
        assert first_latest.id == first_price_id
        assert second_latest.id == second_price_id
        assert first_latest.asset_id == first.id
        assert second_latest.asset_id == second.id
        assert first_latest.price != second_latest.price

        dashboard = container.portfolio_dashboard_query_service.get_dashboard(
            GetPortfolioDashboardQuery(portfolio_id=portfolio.id)
        )
        assert [item.asset_id for item in dashboard.positions] == [first.id, second.id]
        assert [item.currency for item in dashboard.positions] == [Currency.TRY, Currency.USD]
        assert [item.currency for item in dashboard.currencies] == [
            Currency.TRY,
            Currency.USD,
        ]
        window = MainWindow(container)
        asset_table = child(window, QTableWidget, "assetRegistryTable")
        position_table = child(window, QTableWidget, "positionTable")
        currency_table = child(window, QTableWidget, "currencyValuationTable")
        assert asset_table.rowCount() == 2
        assert [cell(asset_table, row, 0).text() for row in range(2)] == ["DUP", "DUP"]
        assert [cell(asset_table, row, 0).data(Qt.ItemDataRole.UserRole) for row in range(2)] == [
            str(first.id),
            str(second.id),
        ]
        assert position_table.rowCount() == 2
        for row, position in enumerate(dashboard.positions):
            assert_position_row(position_table, row, position)
        assert currency_table.rowCount() == 2
        for row, valuation in enumerate(dashboard.currencies):
            assert_currency_row(currency_table, row, valuation)
        assert child(window, QLabel, "noFxNotice").text() == (
            "Values are shown separately by currency. No FX conversion is applied."
        )
        visible_text = " ".join(label.text() for label in window.findChildren(QLabel))
        assert "Total Portfolio Value" not in visible_text
        assert errors == []
    finally:
        close_window(window, qapplication)
        manager.shutdown()


def test_real_closed_position_needs_no_price_and_reopens_from_persisted_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qapplication: QApplication,
) -> None:
    """Value a real full close without price, then reopen with persisted realized P&L."""
    settings, _ = make_settings(tmp_path, "reopen")
    manager = DatabaseManager(settings).initialize()
    closed_window: MainWindow | None = None
    reopened_window: MainWindow | None = None
    try:
        container = build_container(settings, manager)
        errors = capture_critical_errors(monkeypatch)
        portfolio = container.portfolio_application_service.create_portfolio(
            CreatePortfolioCommand(portfolio_name="Closed and reopened")
        )
        asset = create_asset(
            container,
            symbol="cycle",
            name="Cycle Asset",
            currency=Currency.TRY,
        )
        buy_asset(
            container,
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            quantity=Decimal("8.500000000000000001"),
            unit_price=Decimal("50.250000000000000001"),
            occurred_at=BUY_AT,
        )
        sell_asset(
            container,
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            quantity=Decimal("8.500000000000000001"),
            unit_price=Decimal("70.750000000000000001"),
            occurred_at=SELL_AT,
        )

        closed_dashboard = container.portfolio_dashboard_query_service.get_dashboard(
            GetPortfolioDashboardQuery(portfolio_id=portfolio.id)
        )
        assert len(closed_dashboard.positions) == 1
        closed_position = closed_dashboard.positions[0]
        assert closed_position.quantity == Decimal("0")
        assert closed_position.market_price is None
        assert closed_position.price_observed_at is None
        assert closed_position.realized_pnl != Decimal("0")
        closed_window = MainWindow(container)
        closed_table = child(closed_window, QTableWidget, "positionTable")
        assert_position_row(closed_table, 0, closed_position)
        assert cell(closed_table, 0, 3).text() == "Closed"
        assert cell(closed_table, 0, 7).text() == EM_DASH
        assert cell(closed_table, 0, 12).text() == EM_DASH
        assert errors == []
        close_window(closed_window, qapplication)
        closed_window = None

        buy_asset(
            container,
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            quantity=Decimal("3.125000000000000001"),
            unit_price=Decimal("80.875000000000000001"),
            occurred_at=REOPEN_AT,
        )
        recorded_price_id = record_price(
            container,
            asset_id=asset.id,
            price=Decimal("91.625000000000000001"),
            observed_at=REOPEN_PRICE_AT,
        )
        reopened_dashboard = container.portfolio_dashboard_query_service.get_dashboard(
            GetPortfolioDashboardQuery(portfolio_id=portfolio.id)
        )
        assert len(reopened_dashboard.positions) == 1
        reopened_position = reopened_dashboard.positions[0]
        assert reopened_position.quantity > Decimal("0")
        assert reopened_position.market_price == Decimal("91.625000000000000001")
        assert reopened_position.price_observed_at == REOPEN_PRICE_AT
        assert reopened_position.realized_pnl == closed_position.realized_pnl
        assert reopened_position.average_cost != Decimal("0")
        assert reopened_position.cost_basis != Decimal("0")
        latest = container.market_price_application_service.get_latest_market_price(
            GetLatestMarketPriceQuery(asset_id=asset.id)
        )
        assert latest is not None
        assert latest.id == recorded_price_id

        reopened_window = MainWindow(container)
        reopened_table = child(reopened_window, QTableWidget, "positionTable")
        assert_position_row(reopened_table, 0, reopened_position)
        assert cell(reopened_table, 0, 3).text() == "Open"
        assert errors == []
    finally:
        close_window(reopened_window, qapplication)
        close_window(closed_window, qapplication)
        manager.shutdown()


def test_two_real_containers_keep_temporary_databases_isolated(tmp_path: Path) -> None:
    """Prevent service, UnitOfWork, Session, and persisted-state leakage."""
    first_settings, first_database = make_settings(tmp_path, "isolation-first")
    second_settings, second_database = make_settings(tmp_path, "isolation-second")
    first_manager = DatabaseManager(first_settings).initialize()
    second_manager = DatabaseManager(second_settings).initialize()
    try:
        first = build_container(first_settings, first_manager)
        second = build_container(second_settings, second_manager)
        assert_real_service_graph(first)
        assert_real_service_graph(second)
        assert first is not second
        assert first.session_factory is not second.session_factory
        assert first.unit_of_work_factory is not second.unit_of_work_factory
        assert first.portfolio_application_service is not second.portfolio_application_service
        assert first.asset_application_service is not second.asset_application_service
        assert first_database != second_database

        portfolio = first.portfolio_application_service.create_portfolio(
            CreatePortfolioCommand(portfolio_name="First database only")
        )
        asset = create_asset(
            first,
            symbol="ONLY",
            name="First Database Asset",
            currency=Currency.USD,
        )

        assert [
            item.id
            for item in first.portfolio_application_service.list_portfolios(ListPortfoliosQuery())
        ] == [portfolio.id]
        assert [
            item.id for item in first.asset_application_service.list_assets(ListAssetsQuery())
        ] == [asset.id]
        assert second.portfolio_application_service.list_portfolios(ListPortfoliosQuery()) == ()
        assert second.asset_application_service.list_assets(ListAssetsQuery()) == ()
    finally:
        first_manager.shutdown()
        second_manager.shutdown()
