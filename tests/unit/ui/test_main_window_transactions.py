"""Focused transaction and manual-price workflow tests."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
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
    GetPortfolioQuery,
    ListAssetsQuery,
    ListPortfoliosQuery,
)
from app.application.results import (
    AssetPositionView,
    AssetView,
    MarketPriceView,
    PortfolioDetails,
    PortfolioSummary,
    TransactionView,
)
from app.core.container import Container
from app.domain.entities.asset import AssetType
from app.domain.entities.transaction import TransactionType
from app.domain.value_objects.currency import Currency
from app.ui.windows import main_window as main_window_module
from app.ui.windows.main_window import MainWindow

CREATED_AT = datetime(2026, 7, 18, 9, 30, tzinfo=UTC)
TRADE_AT = datetime(2026, 7, 20, 12, 34, 56, 789000, tzinfo=UTC)
PRICE_AT = datetime(2026, 7, 20, 13, 45, 12, 345000, tzinfo=UTC)


def summary(
    name: str,
    *,
    portfolio_id: UUID | None = None,
) -> PortfolioSummary:
    """Build a stable Portfolio summary."""
    return PortfolioSummary(
        id=portfolio_id or uuid4(),
        name=name,
        base_currency=Currency.TRY,
        is_archived=False,
        created_at=CREATED_AT,
    )


def global_asset(
    symbol: str,
    name: str,
    *,
    asset_id: UUID | None = None,
    currency: Currency = Currency.TRY,
) -> AssetView:
    """Build one globally registered Asset."""
    return AssetView(
        id=asset_id or uuid4(),
        symbol=symbol,
        name=name,
        asset_type=AssetType.EQUITY,
        currency=currency,
        is_active=True,
        created_at=CREATED_AT,
    )


def position_asset(asset: AssetView) -> AssetPositionView:
    """Map a global Asset into the existing PortfolioDetails shape."""
    return AssetPositionView(
        id=asset.id,
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type,
        currency=asset.currency,
        is_active=asset.is_active,
        created_at=asset.created_at,
    )


def transaction(
    asset_id: UUID,
    transaction_type: TransactionType,
    *,
    transaction_id: UUID | None = None,
    quantity: str = "10.0000",
    price: str = "123.4500",
    commission: str = "1.2300",
    tax: str = "0.0400",
    date: datetime = TRADE_AT,
) -> TransactionView:
    """Build an exact transaction DTO."""
    return TransactionView(
        id=transaction_id or uuid4(),
        asset_id=asset_id,
        quantity=Decimal(quantity),
        price=Decimal(price),
        transaction_type=transaction_type,
        commission=Decimal(commission),
        tax=Decimal(tax),
        date=date,
    )


def details(
    portfolio: PortfolioSummary,
    *,
    assets: tuple[AssetPositionView, ...] = (),
    transactions: tuple[TransactionView, ...] = (),
) -> PortfolioDetails:
    """Build selected Portfolio details."""
    return PortfolioDetails(
        id=portfolio.id,
        name=portfolio.name,
        base_currency=portfolio.base_currency,
        assets=assets,
        transactions=transactions,
        is_archived=portfolio.is_archived,
        created_at=portfolio.created_at,
    )


class StatefulPortfolioService:
    """Record management calls and expose fake persisted PortfolioDetails."""

    def __init__(
        self,
        portfolios: tuple[PortfolioSummary, ...] = (),
        portfolio_details: tuple[PortfolioDetails, ...] = (),
    ) -> None:
        self.portfolios = portfolios
        self.details_by_id = {item.id: item for item in portfolio_details}
        self.list_calls: list[ListPortfoliosQuery] = []
        self.get_calls: list[GetPortfolioQuery] = []
        self.create_calls: list[CreatePortfolioCommand] = []
        self.buy_calls: list[BuyAssetCommand] = []
        self.sell_calls: list[SellAssetCommand] = []
        self.delete_calls: list[DeleteTransactionCommand] = []
        self.get_error: Exception | None = None
        self.buy_error: Exception | None = None
        self.sell_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.create_result: PortfolioDetails | None = None
        self.buy_result: TransactionView | None = None
        self.sell_result: TransactionView | None = None
        self.delete_result: PortfolioDetails | None = None
        self.details_after_buy: PortfolioDetails | None = None
        self.details_after_sell: PortfolioDetails | None = None

    def list_portfolios(
        self,
        query: ListPortfoliosQuery,
    ) -> tuple[PortfolioSummary, ...]:
        self.list_calls.append(query)
        return self.portfolios

    def get_portfolio(self, query: GetPortfolioQuery) -> PortfolioDetails:
        self.get_calls.append(query)
        if self.get_error is not None:
            raise self.get_error
        return self.details_by_id[query.portfolio_id]

    def create_portfolio(self, command: CreatePortfolioCommand) -> PortfolioDetails:
        self.create_calls.append(command)
        if self.create_result is None:
            raise AssertionError("A creation result is required.")
        created = self.create_result
        self.portfolios = (
            *self.portfolios,
            PortfolioSummary(
                id=created.id,
                name=created.name,
                base_currency=created.base_currency,
                is_archived=created.is_archived,
                created_at=created.created_at,
            ),
        )
        self.details_by_id[created.id] = created
        return created

    def buy_asset(self, command: BuyAssetCommand) -> TransactionView:
        self.buy_calls.append(command)
        if self.buy_error is not None:
            raise self.buy_error
        if self.buy_result is None or self.details_after_buy is None:
            raise AssertionError("BUY results are required.")
        self.details_by_id[command.portfolio_id] = self.details_after_buy
        return self.buy_result

    def sell_asset(self, command: SellAssetCommand) -> TransactionView:
        self.sell_calls.append(command)
        if self.sell_error is not None:
            raise self.sell_error
        if self.sell_result is None or self.details_after_sell is None:
            raise AssertionError("SELL results are required.")
        self.details_by_id[command.portfolio_id] = self.details_after_sell
        return self.sell_result

    def delete_transaction(self, command: DeleteTransactionCommand) -> PortfolioDetails:
        self.delete_calls.append(command)
        if self.delete_error is not None:
            raise self.delete_error
        if self.delete_result is None:
            raise AssertionError("A deletion result is required.")
        self.details_by_id[command.portfolio_id] = self.delete_result
        return self.delete_result


class StatefulAssetService:
    """Expose an ordered fake global Asset registry."""

    def __init__(self, assets: tuple[AssetView, ...] = ()) -> None:
        self.assets = assets
        self.list_calls: list[ListAssetsQuery] = []
        self.create_calls: list[CreateAssetCommand] = []
        self.create_result: AssetView | None = None

    def list_assets(self, query: ListAssetsQuery) -> tuple[AssetView, ...]:
        self.list_calls.append(query)
        return self.assets

    def create_asset(self, command: CreateAssetCommand) -> AssetView:
        self.create_calls.append(command)
        if self.create_result is None:
            raise AssertionError("An Asset creation result is required.")
        self.assets = (*self.assets, self.create_result)
        return self.create_result


class StatefulMarketPriceService:
    """Record manual writes and reject unrequested follow-up reads."""

    def __init__(self) -> None:
        self.record_calls: list[RecordMarketPriceCommand] = []
        self.latest_calls: list[GetLatestMarketPriceQuery] = []
        self.record_result: MarketPriceView | None = None
        self.record_error: Exception | None = None

    def record_market_price(self, command: RecordMarketPriceCommand) -> MarketPriceView:
        self.record_calls.append(command)
        if self.record_error is not None:
            raise self.record_error
        if self.record_result is None:
            raise AssertionError("A market-price result is required.")
        return self.record_result

    def get_latest_market_price(
        self,
        query: GetLatestMarketPriceQuery,
    ) -> MarketPriceView | None:
        self.latest_calls.append(query)
        raise AssertionError("MainWindow must not read latest prices in Commit 8.")


class TransactionWindowFactory:
    """Construct and release MainWindows around fake application services."""

    def __init__(self, application: QApplication) -> None:
        self.application = application
        self.windows: list[MainWindow] = []

    def __call__(
        self,
        portfolios: StatefulPortfolioService,
        assets: StatefulAssetService,
        prices: StatefulMarketPriceService,
    ) -> MainWindow:
        container = cast(
            Container,
            SimpleNamespace(
                settings=SimpleNamespace(app_name="Mira Transaction Test"),
                portfolio_application_service=portfolios,
                asset_application_service=assets,
                market_price_application_service=prices,
            ),
        )
        window = MainWindow(container)
        self.windows.append(window)
        return window

    def close_all(self) -> None:
        """Release tracked Qt widgets."""
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.application.processEvents()


@pytest.fixture
def make_window(
    qapplication: QApplication,
) -> Iterator[TransactionWindowFactory]:
    """Yield a tracked MainWindow factory."""
    factory = TransactionWindowFactory(qapplication)
    yield factory
    factory.close_all()


def capture_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[QMainWindow, str, str]]:
    """Replace modal critical dialogs with a recorder."""
    errors: list[tuple[QMainWindow, str, str]] = []

    def record(parent: QMainWindow, title: str, message: str) -> None:
        errors.append((parent, title, message))

    monkeypatch.setattr(QMessageBox, "critical", record)
    return errors


def test_initial_details_load_renders_exact_ordered_transactions_and_actions(
    make_window: TransactionWindowFactory,
) -> None:
    portfolio = summary("Transactions")
    first_global = global_asset("DUP", "First", currency=Currency.TRY)
    second_global = global_asset("DUP", "Second", currency=Currency.USD)
    first_position = position_asset(first_global)
    second_position = position_asset(second_global)
    buy = transaction(
        first_global.id,
        TransactionType.BUY,
        quantity="10.000000000000000001",
        price="0.000000000000000009",
        commission="1.2300",
        tax="0.0400",
    )
    sell = transaction(
        second_global.id,
        TransactionType.SELL,
        quantity="2.5000",
        price="200.0000",
        commission="0.0000",
        tax="3.1400",
    )
    portfolio_service = StatefulPortfolioService(
        (portfolio,),
        (details(portfolio, assets=(first_position, second_position), transactions=(buy, sell)),),
    )
    asset_service = StatefulAssetService((first_global, second_global))
    price_service = StatefulMarketPriceService()

    window = make_window(portfolio_service, asset_service, price_service)
    table = window.findChild(QTableWidget, "transactionTable")

    assert portfolio_service.list_calls == [ListPortfoliosQuery()]
    assert portfolio_service.get_calls == [GetPortfolioQuery(portfolio_id=portfolio.id)]
    assert asset_service.list_calls == [ListAssetsQuery()]
    assert price_service.record_calls == []
    assert price_service.latest_calls == []
    assert window._selected_portfolio_details == portfolio_service.details_by_id[portfolio.id]
    assert table is not None
    assert table.rowCount() == 2
    assert [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())] == [
        "Date",
        "Type",
        "Asset",
        "Quantity",
        "Unit price",
        "Currency",
        "Commission",
        "Tax",
    ]
    assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert [table.item(row, 1).text() for row in range(2)] == ["Buy", "Sell"]
    assert [table.item(row, 2).text() for row in range(2)] == [
        "DUP — First",
        "DUP — Second",
    ]
    assert table.item(0, 3).text() == "10.000000000000000001"
    assert table.item(0, 4).text() == "9E-18"
    assert table.item(0, 5).text() == "TRY"
    assert table.item(0, 6).text() == "1.2300"
    assert table.item(0, 7).text() == "0.0400"
    assert table.item(0, 0).text() == "2026-07-20 12:34:56"
    assert table.item(0, 0).data(Qt.ItemDataRole.UserRole) == str(buy.id)
    assert table.item(1, 0).data(Qt.ItemDataRole.UserRole) == str(sell.id)
    assert table.horizontalHeaderItem(8) is None

    buy_button = window.findChild(QPushButton, "buyAssetButton")
    sell_button = window.findChild(QPushButton, "sellAssetButton")
    price_button = window.findChild(QPushButton, "recordPriceButton")
    delete_button = window.findChild(QPushButton, "deleteTransactionButton")
    assert buy_button.isEnabled()
    assert sell_button.isEnabled()
    assert price_button.isEnabled()
    assert not delete_button.isEnabled()
    table.selectRow(1)
    assert window._selected_transaction_id == sell.id
    assert delete_button.isEnabled()


def test_selection_loads_once_per_identity_and_failure_clears_stale_history(
    monkeypatch: pytest.MonkeyPatch,
    make_window: TransactionWindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    first = summary("First")
    second = summary("Second")
    asset = global_asset("ONE", "One")
    first_transaction = transaction(asset.id, TransactionType.BUY)
    portfolio_service = StatefulPortfolioService(
        (first, second),
        (
            details(
                first,
                assets=(position_asset(asset),),
                transactions=(first_transaction,),
            ),
            details(second),
        ),
    )
    asset_service = StatefulAssetService((asset,))
    price_service = StatefulMarketPriceService()
    window = make_window(portfolio_service, asset_service, price_service)
    selector = window.findChild(QComboBox, "portfolioSelector")
    table = window.findChild(QTableWidget, "transactionTable")

    assert selector is not None
    selector.setCurrentIndex(1)
    assert portfolio_service.get_calls == [
        GetPortfolioQuery(portfolio_id=first.id),
        GetPortfolioQuery(portfolio_id=second.id),
    ]
    assert window._refresh_portfolios()
    assert len(portfolio_service.get_calls) == 2

    portfolio_service.get_error = ValidationError("Details unavailable.")
    selector.setCurrentIndex(0)
    assert portfolio_service.get_calls[-1] == GetPortfolioQuery(portfolio_id=first.id)
    assert window._selected_portfolio_id == first.id
    assert window._selected_portfolio_details is None
    assert table is not None
    assert table.rowCount() == 0
    assert window.findChild(QTableWidget, "assetRegistryTable").rowCount() == 1
    assert len(errors) == 1
    assert "Details unavailable." in errors[0][2]
    assert window.statusBar().currentMessage() == "Unable to load Portfolio details"
    assert window.findChild(QPushButton, "buyAssetButton").isEnabled()
    assert not window.findChild(QPushButton, "sellAssetButton").isEnabled()


def test_action_enabling_tracks_portfolio_assets_and_global_assets(
    make_window: TransactionWindowFactory,
) -> None:
    portfolio = summary("Empty")
    portfolio_service = StatefulPortfolioService((portfolio,), (details(portfolio),))
    window = make_window(
        portfolio_service,
        StatefulAssetService(),
        StatefulMarketPriceService(),
    )

    assert not window.findChild(QPushButton, "buyAssetButton").isEnabled()
    assert not window.findChild(QPushButton, "sellAssetButton").isEnabled()
    assert not window.findChild(QPushButton, "recordPriceButton").isEnabled()
    assert not window.findChild(QPushButton, "deleteTransactionButton").isEnabled()
    assert window.findChild(QTableWidget, "transactionTable").rowCount() == 0
    assert window.findChild(QLabel, "transactionEmptyState").text() == (
        "No transactions have been recorded for this portfolio."
    )

    global_only = global_asset("GLOBAL", "Global only")
    no_portfolio_window = make_window(
        StatefulPortfolioService(),
        StatefulAssetService((global_only,)),
        StatefulMarketPriceService(),
    )
    assert not no_portfolio_window.findChild(QPushButton, "buyAssetButton").isEnabled()
    assert not no_portfolio_window.findChild(QPushButton, "sellAssetButton").isEnabled()
    assert no_portfolio_window.findChild(QPushButton, "recordPriceButton").isEnabled()
    assert not no_portfolio_window.findChild(QPushButton, "deleteTransactionButton").isEnabled()
    assert no_portfolio_window._selected_portfolio_details is None
    assert no_portfolio_window.findChild(QLabel, "transactionEmptyState").text() == (
        "Create or select a portfolio to record transactions."
    )


class AcceptedTradeDialog:
    """Expose exact values without creating a nested Qt event loop."""

    expected_type = TransactionType.BUY
    expected_assets: tuple[AssetView | AssetPositionView, ...] = ()
    expected_parent: MainWindow | None = None
    selected_asset_id: UUID

    def __init__(
        self,
        transaction_type: TransactionType,
        assets: tuple[AssetView | AssetPositionView, ...],
        parent: MainWindow,
    ) -> None:
        assert transaction_type is self.expected_type
        assert assets == self.expected_assets
        assert parent is self.expected_parent

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def asset_id(self) -> UUID:
        return self.selected_asset_id

    def quantity(self) -> Decimal:
        return Decimal("10.000000000000000001")

    def unit_price(self) -> Decimal:
        return Decimal("123.4500")

    def trade_datetime(self) -> datetime:
        return TRADE_AT

    def commission(self) -> Decimal:
        return Decimal("1.2300")

    def tax(self) -> Decimal:
        return Decimal("0.0400")


def test_buy_success_uses_global_assets_and_refreshes_details_once(
    monkeypatch: pytest.MonkeyPatch,
    make_window: TransactionWindowFactory,
) -> None:
    portfolio = summary("Buy")
    first = global_asset("DUP", "First")
    second = global_asset("DUP", "Second", currency=Currency.USD)
    initial = details(portfolio)
    created = transaction(first.id, TransactionType.BUY)
    refreshed = details(
        portfolio,
        assets=(position_asset(first),),
        transactions=(created,),
    )
    portfolio_service = StatefulPortfolioService((portfolio,), (initial,))
    portfolio_service.buy_result = created
    portfolio_service.details_after_buy = refreshed
    asset_service = StatefulAssetService((first, second))
    price_service = StatefulMarketPriceService()
    window = make_window(portfolio_service, asset_service, price_service)

    AcceptedTradeDialog.expected_type = TransactionType.BUY
    AcceptedTradeDialog.expected_assets = (first, second)
    AcceptedTradeDialog.expected_parent = window
    AcceptedTradeDialog.selected_asset_id = first.id
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", AcceptedTradeDialog)

    window._buy_asset()

    assert portfolio_service.buy_calls == [
        BuyAssetCommand(
            portfolio_id=portfolio.id,
            asset_id=first.id,
            quantity=Decimal("10.000000000000000001"),
            unit_price=Decimal("123.4500"),
            trade_datetime=TRADE_AT,
            commission=Decimal("1.2300"),
            tax=Decimal("0.0400"),
        )
    ]
    assert portfolio_service.get_calls == [
        GetPortfolioQuery(portfolio_id=portfolio.id),
        GetPortfolioQuery(portfolio_id=portfolio.id),
    ]
    assert portfolio_service.list_calls == [ListPortfoliosQuery()]
    assert asset_service.list_calls == [ListAssetsQuery()]
    assert price_service.record_calls == []
    assert window._selected_portfolio_id == portfolio.id
    assert window._selected_transaction_id == created.id
    assert window.findChild(QTableWidget, "transactionTable").currentRow() == 0
    assert window.statusBar().currentMessage() == "Buy transaction recorded"


def test_buy_cancel_parse_failure_and_service_failure_are_atomic(
    monkeypatch: pytest.MonkeyPatch,
    make_window: TransactionWindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    portfolio = summary("Atomic buy")
    asset = global_asset("ONE", "One")
    existing = transaction(asset.id, TransactionType.BUY)
    current = details(
        portfolio,
        assets=(position_asset(asset),),
        transactions=(existing,),
    )
    portfolio_service = StatefulPortfolioService((portfolio,), (current,))
    asset_service = StatefulAssetService((asset,))
    window = make_window(
        portfolio_service,
        asset_service,
        StatefulMarketPriceService(),
    )

    class RejectedDialog(AcceptedTradeDialog):
        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Rejected

    RejectedDialog.expected_type = TransactionType.BUY
    RejectedDialog.expected_assets = (asset,)
    RejectedDialog.expected_parent = window
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", RejectedDialog)
    window._buy_asset()
    assert portfolio_service.buy_calls == []
    assert len(portfolio_service.get_calls) == 1

    class InvalidDialog(RejectedDialog):
        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def quantity(self) -> Decimal:
            raise ValueError("Quantity must be greater than zero.")

    monkeypatch.setattr(main_window_module, "RecordTradeDialog", InvalidDialog)
    window._buy_asset()
    assert portfolio_service.buy_calls == []
    assert len(portfolio_service.get_calls) == 1
    assert errors[-1][1] == "Unable to record transaction"

    AcceptedTradeDialog.expected_type = TransactionType.BUY
    AcceptedTradeDialog.expected_assets = (asset,)
    AcceptedTradeDialog.expected_parent = window
    AcceptedTradeDialog.selected_asset_id = asset.id
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", AcceptedTradeDialog)
    portfolio_service.buy_error = ValidationError("BUY rejected.")
    window._buy_asset()

    assert len(portfolio_service.buy_calls) == 1
    assert len(portfolio_service.get_calls) == 1
    assert window.findChild(QTableWidget, "transactionTable").rowCount() == 1
    assert window.findChild(QTableWidget, "transactionTable").item(0, 0).data(
        Qt.ItemDataRole.UserRole
    ) == str(existing.id)
    assert "BUY rejected." in errors[-1][2]
    assert window.statusBar().currentMessage() == "Unable to record transaction"


def test_sell_success_supplies_only_portfolio_assets_without_position_math(
    monkeypatch: pytest.MonkeyPatch,
    make_window: TransactionWindowFactory,
) -> None:
    portfolio = summary("Sell")
    owned = global_asset("OWN", "Owned")
    unowned = global_asset("OTHER", "Unowned")
    owned_position = position_asset(owned)
    initial_buy = transaction(owned.id, TransactionType.BUY)
    created_sell = transaction(
        owned.id,
        TransactionType.SELL,
        quantity="999999.0000",
    )
    initial = details(
        portfolio,
        assets=(owned_position,),
        transactions=(initial_buy,),
    )
    refreshed = details(
        portfolio,
        assets=(owned_position,),
        transactions=(initial_buy, created_sell),
    )
    portfolio_service = StatefulPortfolioService((portfolio,), (initial,))
    portfolio_service.sell_result = created_sell
    portfolio_service.details_after_sell = refreshed
    asset_service = StatefulAssetService((owned, unowned))
    window = make_window(
        portfolio_service,
        asset_service,
        StatefulMarketPriceService(),
    )

    AcceptedTradeDialog.expected_type = TransactionType.SELL
    AcceptedTradeDialog.expected_assets = (owned_position,)
    AcceptedTradeDialog.expected_parent = window
    AcceptedTradeDialog.selected_asset_id = owned.id
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", AcceptedTradeDialog)
    window._sell_asset()

    assert portfolio_service.sell_calls == [
        SellAssetCommand(
            portfolio_id=portfolio.id,
            asset_id=owned.id,
            quantity=Decimal("10.000000000000000001"),
            unit_price=Decimal("123.4500"),
            trade_datetime=TRADE_AT,
            commission=Decimal("1.2300"),
            tax=Decimal("0.0400"),
        )
    ]
    assert len(portfolio_service.get_calls) == 2
    assert asset_service.list_calls == [ListAssetsQuery()]
    assert window._selected_transaction_id == created_sell.id
    assert window.statusBar().currentMessage() == "Sell transaction recorded"


def test_sell_cancel_and_service_failure_leave_history_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    make_window: TransactionWindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    portfolio = summary("Atomic sell")
    asset = global_asset("OWN", "Owned")
    position = position_asset(asset)
    existing = transaction(asset.id, TransactionType.BUY)
    current = details(portfolio, assets=(position,), transactions=(existing,))
    portfolio_service = StatefulPortfolioService((portfolio,), (current,))
    window = make_window(
        portfolio_service,
        StatefulAssetService((asset,)),
        StatefulMarketPriceService(),
    )

    class RejectedSellDialog(AcceptedTradeDialog):
        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Rejected

    RejectedSellDialog.expected_type = TransactionType.SELL
    RejectedSellDialog.expected_assets = (position,)
    RejectedSellDialog.expected_parent = window
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", RejectedSellDialog)
    window._sell_asset()
    assert portfolio_service.sell_calls == []
    assert len(portfolio_service.get_calls) == 1

    AcceptedTradeDialog.expected_type = TransactionType.SELL
    AcceptedTradeDialog.expected_assets = (position,)
    AcceptedTradeDialog.expected_parent = window
    AcceptedTradeDialog.selected_asset_id = asset.id
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", AcceptedTradeDialog)
    portfolio_service.sell_error = ValidationError("SELL rejected.")
    window._sell_asset()

    table = window.findChild(QTableWidget, "transactionTable")
    assert len(portfolio_service.sell_calls) == 1
    assert len(portfolio_service.get_calls) == 1
    assert table.rowCount() == 1
    assert table.item(0, 0).data(Qt.ItemDataRole.UserRole) == str(existing.id)
    assert "SELL rejected." in errors[-1][2]
    assert window.statusBar().currentMessage() == "Unable to record transaction"


def test_missing_transaction_asset_metadata_uses_safe_nonfinancial_fallback(
    make_window: TransactionWindowFactory,
) -> None:
    portfolio = summary("Inconsistent details")
    missing_asset_id = uuid4()
    existing = transaction(missing_asset_id, TransactionType.BUY)
    portfolio_service = StatefulPortfolioService(
        (portfolio,),
        (details(portfolio, transactions=(existing,)),),
    )
    window = make_window(
        portfolio_service,
        StatefulAssetService(),
        StatefulMarketPriceService(),
    )
    table = window.findChild(QTableWidget, "transactionTable")

    assert table.rowCount() == 1
    assert table.item(0, 2).text() == str(missing_asset_id)
    assert table.item(0, 5).text() == "—"


def test_delete_uses_row_uuid_and_returned_details_without_follow_up_read(
    monkeypatch: pytest.MonkeyPatch,
    make_window: TransactionWindowFactory,
) -> None:
    portfolio = summary("Delete")
    asset = global_asset("ONE", "One")
    position = position_asset(asset)
    existing = transaction(asset.id, TransactionType.BUY)
    initial = details(portfolio, assets=(position,), transactions=(existing,))
    after_delete = details(portfolio, assets=(position,))
    portfolio_service = StatefulPortfolioService((portfolio,), (initial,))
    portfolio_service.delete_result = after_delete
    window = make_window(
        portfolio_service,
        StatefulAssetService((asset,)),
        StatefulMarketPriceService(),
    )
    table = window.findChild(QTableWidget, "transactionTable")
    table.selectRow(0)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )

    window._delete_selected_transaction()

    assert portfolio_service.delete_calls == [
        DeleteTransactionCommand(
            portfolio_id=portfolio.id,
            transaction_id=existing.id,
        )
    ]
    assert portfolio_service.get_calls == [GetPortfolioQuery(portfolio_id=portfolio.id)]
    assert portfolio_service.list_calls == [ListPortfoliosQuery()]
    assert window._selected_portfolio_id == portfolio.id
    assert window._selected_portfolio_details == after_delete
    assert table.rowCount() == 0
    assert window.findChild(QPushButton, "sellAssetButton").isEnabled()
    assert not window.findChild(QPushButton, "deleteTransactionButton").isEnabled()
    assert window.statusBar().currentMessage() == "Transaction deleted"


def test_delete_cancel_and_failure_preserve_existing_row_and_selection(
    monkeypatch: pytest.MonkeyPatch,
    make_window: TransactionWindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    portfolio = summary("Delete atomicity")
    asset = global_asset("ONE", "One")
    existing = transaction(asset.id, TransactionType.BUY)
    initial = details(
        portfolio,
        assets=(position_asset(asset),),
        transactions=(existing,),
    )
    portfolio_service = StatefulPortfolioService((portfolio,), (initial,))
    window = make_window(
        portfolio_service,
        StatefulAssetService((asset,)),
        StatefulMarketPriceService(),
    )
    table = window.findChild(QTableWidget, "transactionTable")
    table.selectRow(0)

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.No,
    )
    window._delete_selected_transaction()
    assert portfolio_service.delete_calls == []
    assert table.rowCount() == 1
    assert window._selected_transaction_id == existing.id

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    portfolio_service.delete_error = ValidationError("Delete rejected.")
    window._delete_selected_transaction()
    assert len(portfolio_service.delete_calls) == 1
    assert len(portfolio_service.get_calls) == 1
    assert table.rowCount() == 1
    assert window._selected_transaction_id == existing.id
    assert "Delete rejected." in errors[-1][2]
    assert window.statusBar().currentMessage() == "Unable to delete transaction"


class AcceptedPriceDialog:
    """Expose one exact manual-price payload."""

    expected_assets: tuple[AssetView, ...] = ()
    expected_parent: MainWindow | None = None
    selected_asset_id: UUID
    entered_price = Decimal("0.0000")

    def __init__(self, assets: tuple[AssetView, ...], parent: MainWindow) -> None:
        assert assets == self.expected_assets
        assert parent is self.expected_parent

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def asset_id(self) -> UUID:
        return self.selected_asset_id

    def price(self) -> Decimal:
        return self.entered_price

    def observed_at(self) -> datetime:
        return PRICE_AT


def test_manual_zero_price_writes_once_without_reads_or_ui_refresh(
    monkeypatch: pytest.MonkeyPatch,
    make_window: TransactionWindowFactory,
) -> None:
    portfolio = summary("Price")
    asset = global_asset("PRICE", "Priced", currency=Currency.GBP)
    portfolio_service = StatefulPortfolioService((portfolio,), (details(portfolio),))
    asset_service = StatefulAssetService((asset,))
    price_service = StatefulMarketPriceService()
    price_service.record_result = MarketPriceView(
        id=uuid4(),
        asset_id=asset.id,
        price=Decimal("0.0000"),
        currency=Currency.GBP,
        observed_at=PRICE_AT,
    )
    window = make_window(portfolio_service, asset_service, price_service)

    AcceptedPriceDialog.expected_assets = (asset,)
    AcceptedPriceDialog.expected_parent = window
    AcceptedPriceDialog.selected_asset_id = asset.id
    AcceptedPriceDialog.entered_price = Decimal("0.0000")
    monkeypatch.setattr(
        main_window_module,
        "RecordMarketPriceDialog",
        AcceptedPriceDialog,
    )
    window._record_market_price()

    assert price_service.record_calls == [
        RecordMarketPriceCommand(
            asset_id=asset.id,
            price=Decimal("0.0000"),
            observed_at=PRICE_AT,
        )
    ]
    assert price_service.latest_calls == []
    assert portfolio_service.get_calls == [GetPortfolioQuery(portfolio_id=portfolio.id)]
    assert portfolio_service.list_calls == [ListPortfoliosQuery()]
    assert asset_service.list_calls == [ListAssetsQuery()]
    assert window.findChild(QTableWidget, "transactionTable").rowCount() == 0
    assert window.statusBar().currentMessage() == "Price recorded for PRICE: 0.0000 GBP"


def test_manual_price_cancel_and_failure_have_no_follow_up_or_state_mutation(
    monkeypatch: pytest.MonkeyPatch,
    make_window: TransactionWindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    asset = global_asset("PRICE", "Priced")
    portfolio = summary("Price errors")
    portfolio_service = StatefulPortfolioService((portfolio,), (details(portfolio),))
    asset_service = StatefulAssetService((asset,))
    price_service = StatefulMarketPriceService()
    window = make_window(portfolio_service, asset_service, price_service)

    class RejectedPriceDialog(AcceptedPriceDialog):
        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Rejected

    RejectedPriceDialog.expected_assets = (asset,)
    RejectedPriceDialog.expected_parent = window
    monkeypatch.setattr(
        main_window_module,
        "RecordMarketPriceDialog",
        RejectedPriceDialog,
    )
    window._record_market_price()
    assert price_service.record_calls == []

    AcceptedPriceDialog.expected_assets = (asset,)
    AcceptedPriceDialog.expected_parent = window
    AcceptedPriceDialog.selected_asset_id = asset.id
    monkeypatch.setattr(
        main_window_module,
        "RecordMarketPriceDialog",
        AcceptedPriceDialog,
    )
    price_service.record_error = ValidationError("Price rejected.")
    window._record_market_price()

    assert len(price_service.record_calls) == 1
    assert price_service.latest_calls == []
    assert len(portfolio_service.get_calls) == 1
    assert asset_service.list_calls == [ListAssetsQuery()]
    assert "Price rejected." in errors[-1][2]
    assert window.statusBar().currentMessage() == "Unable to record price"


def test_stateful_management_trade_price_delete_smoke(
    monkeypatch: pytest.MonkeyPatch,
    make_window: TransactionWindowFactory,
) -> None:
    portfolio_summary = summary("Alpha")
    created_portfolio = details(portfolio_summary)
    asset = global_asset("FLOW", "Workflow Asset")
    position = position_asset(asset)
    buy = transaction(asset.id, TransactionType.BUY)
    after_buy = details(
        portfolio_summary,
        assets=(position,),
        transactions=(buy,),
    )
    after_delete = details(portfolio_summary, assets=(position,))
    portfolio_service = StatefulPortfolioService()
    portfolio_service.create_result = created_portfolio
    portfolio_service.buy_result = buy
    portfolio_service.details_after_buy = after_buy
    portfolio_service.delete_result = after_delete
    asset_service = StatefulAssetService()
    asset_service.create_result = asset
    price_service = StatefulMarketPriceService()
    price_service.record_result = MarketPriceView(
        id=uuid4(),
        asset_id=asset.id,
        price=Decimal("123.4500"),
        currency=asset.currency,
        observed_at=PRICE_AT,
    )
    window = make_window(portfolio_service, asset_service, price_service)

    class PortfolioDialog:
        def __init__(self, parent: MainWindow) -> None:
            assert parent is window

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def portfolio_name(self) -> str:
            return "Alpha"

    class AssetDialog:
        def __init__(self, parent: MainWindow) -> None:
            assert parent is window

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def symbol(self) -> str:
            return asset.symbol

        def asset_name(self) -> str:
            return asset.name

        def asset_type(self) -> AssetType:
            return asset.asset_type

        def currency(self) -> Currency:
            return asset.currency

    monkeypatch.setattr(main_window_module, "CreatePortfolioDialog", PortfolioDialog)
    monkeypatch.setattr(main_window_module, "CreateAssetDialog", AssetDialog)
    window._create_portfolio()
    window._create_asset()

    AcceptedTradeDialog.expected_type = TransactionType.BUY
    AcceptedTradeDialog.expected_assets = (asset,)
    AcceptedTradeDialog.expected_parent = window
    AcceptedTradeDialog.selected_asset_id = asset.id
    monkeypatch.setattr(main_window_module, "RecordTradeDialog", AcceptedTradeDialog)
    window._buy_asset()
    assert window.findChild(QTableWidget, "transactionTable").rowCount() == 1

    AcceptedPriceDialog.expected_assets = (asset,)
    AcceptedPriceDialog.expected_parent = window
    AcceptedPriceDialog.selected_asset_id = asset.id
    AcceptedPriceDialog.entered_price = Decimal("123.4500")
    monkeypatch.setattr(
        main_window_module,
        "RecordMarketPriceDialog",
        AcceptedPriceDialog,
    )
    window._record_market_price()
    assert window.statusBar().currentMessage() == "Price recorded for FLOW: 123.4500 TRY"

    window.findChild(QTableWidget, "transactionTable").selectRow(0)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    window._delete_selected_transaction()
    assert window.findChild(QTableWidget, "transactionTable").rowCount() == 0
    assert len(portfolio_service.create_calls) == 1
    assert len(asset_service.create_calls) == 1
    assert len(portfolio_service.buy_calls) == 1
    assert len(price_service.record_calls) == 1
    assert len(portfolio_service.delete_calls) == 1


def test_commit_8_ui_source_has_no_dashboard_corporate_formula_or_persistence() -> None:
    source = Path(main_window_module.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "app.infrastructure",
        "sqlalchemy",
        "Session",
        "repositories",
        "UnitOfWork",
        "DatabaseManager",
        "portfolio_dashboard_query_service",
        "GetPortfolioDashboardQuery",
        "PortfolioDashboard",
        "ValuedAssetPositionView",
        "CurrencyValuationView",
        "PortfolioPositionCalculator",
        "PortfolioValuationCalculator",
        "DIVIDEND",
        "RIGHTS_ISSUE",
        "BONUS_ISSUE",
        "STOCK_SPLIT",
        "QDoubleSpinBox",
        "float(",
        "calculate_total",
        "cost_basis",
        "market_value",
        "realized_pnl",
        "unrealized_pnl",
        "QTimer",
        "threading",
        "async def",
    ):
        assert forbidden not in source
