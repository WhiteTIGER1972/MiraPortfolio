"""Focused MainWindow orchestration tests for calculated Portfolio dashboards."""

from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QLabel,
    QPushButton,
    QTableWidget,
)

from app.application.queries import (
    GetPortfolioDashboardQuery,
    GetPortfolioQuery,
    ListAssetsQuery,
    ListPortfoliosQuery,
)
from app.application.results import MarketPriceView, PortfolioDashboard
from app.domain.entities.asset import AssetType
from app.domain.entities.transaction import TransactionType
from app.domain.exceptions import InsufficientPositionError, MissingMarketPriceError
from app.domain.value_objects.currency import Currency
from app.ui.windows import main_window as main_window_module
from app.ui.windows.main_window import MainWindow
from tests.unit.ui.test_main_window_transactions import (
    PRICE_AT,
    AcceptedPriceDialog,
    StatefulAssetService,
    StatefulDashboardService,
    StatefulMarketPriceService,
    StatefulPortfolioService,
    TransactionWindowFactory,
    capture_errors,
    details,
    global_asset,
    position_asset,
    summary,
    transaction,
)
from tests.unit.ui.test_portfolio_valuation_panel import (
    currency_valuation,
    valued_position,
)


@pytest.fixture
def make_dashboard_window(
    qapplication: QApplication,
) -> Iterator[TransactionWindowFactory]:
    """Yield a tracked MainWindow factory."""
    factory = TransactionWindowFactory(qapplication)
    yield factory
    factory.close_all()


def test_initial_load_queries_only_selected_uuid_and_renders_complete_dashboard(
    make_dashboard_window: TransactionWindowFactory,
) -> None:
    portfolio = summary("Calculated")
    asset = global_asset("EXACT", "Exact values")
    buy = transaction(asset.id, TransactionType.BUY)
    selected_details = details(
        portfolio,
        assets=(position_asset(asset),),
        transactions=(buy,),
    )
    portfolio_service = StatefulPortfolioService((portfolio,), (selected_details,))
    asset_service = StatefulAssetService((asset,))
    price_service = StatefulMarketPriceService()
    dashboard_service = StatefulDashboardService(portfolio_service)
    position = valued_position(
        asset.symbol,
        asset.name,
        asset_id=asset.id,
        total_pnl="65.6250000000000000001",
    )
    valuation = currency_valuation(
        Currency.TRY,
        cost_basis="250.0000000000000000025",
        market_value="308.6250000000000000000",
        realized_pnl="7.0000",
        unrealized_pnl="58.6250000000000000000",
        total_pnl="65.6250000000000000001",
    )
    dashboard = PortfolioDashboard(
        portfolio=selected_details,
        positions=(position,),
        currencies=(valuation,),
    )
    dashboard_service.results_by_id[portfolio.id] = dashboard

    window = make_dashboard_window(
        portfolio_service,
        asset_service,
        price_service,
        dashboard_service,
    )

    assert portfolio_service.list_calls == [ListPortfoliosQuery()]
    assert portfolio_service.get_calls == [GetPortfolioQuery(portfolio_id=portfolio.id)]
    assert asset_service.list_calls == [ListAssetsQuery()]
    assert dashboard_service.calls == [GetPortfolioDashboardQuery(portfolio_id=portfolio.id)]
    assert price_service.latest_calls == []
    assert window._portfolio_dashboard is dashboard
    assert window._selected_portfolio_details is selected_details
    assert window.findChild(QTableWidget, "transactionTable").rowCount() == 1
    position_table = window.findChild(QTableWidget, "positionTable")
    currency_table = window.findChild(QTableWidget, "currencyValuationTable")
    assert position_table.rowCount() == 1
    assert position_table.item(0, 0).text() == "EXACT — Exact values"
    assert position_table.item(0, 11).text() == "65.6250000000000000001"
    assert position_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == str(asset.id)
    assert currency_table.rowCount() == 1
    assert currency_table.item(0, 5).text() == "65.6250000000000000001"


def test_no_portfolio_skips_dashboard_and_disables_manual_refresh(
    make_dashboard_window: TransactionWindowFactory,
) -> None:
    portfolio_service = StatefulPortfolioService()
    dashboard_service = StatefulDashboardService(portfolio_service)
    window = make_dashboard_window(
        portfolio_service,
        StatefulAssetService(),
        StatefulMarketPriceService(),
        dashboard_service,
    )

    assert dashboard_service.calls == []
    assert window._portfolio_dashboard is None
    assert window.findChild(QTableWidget, "positionTable").rowCount() == 0
    assert window.findChild(QTableWidget, "currencyValuationTable").rowCount() == 0
    assert window.findChild(QLabel, "valuationState").text() == (
        "Create or select a portfolio to view valuation."
    )
    refresh = window.findChild(QPushButton, "refreshValuationButton")
    assert not refresh.isEnabled()
    refresh.click()
    assert dashboard_service.calls == []


def test_successful_empty_dashboard_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
    make_dashboard_window: TransactionWindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    portfolio = summary("Empty")
    selected_details = details(portfolio)
    portfolio_service = StatefulPortfolioService((portfolio,), (selected_details,))
    dashboard_service = StatefulDashboardService(portfolio_service)

    window = make_dashboard_window(
        portfolio_service,
        StatefulAssetService(),
        StatefulMarketPriceService(),
        dashboard_service,
    )

    assert errors == []
    assert window._portfolio_dashboard == PortfolioDashboard(
        portfolio=selected_details,
        positions=(),
        currencies=(),
    )
    assert window.findChild(QTableWidget, "positionTable").rowCount() == 0
    assert window.findChild(QTableWidget, "currencyValuationTable").rowCount() == 0
    assert window.findChild(QLabel, "valuationState").text() == (
        "No calculated positions yet. Record a BUY transaction to begin."
    )


def test_missing_price_is_inline_and_preserves_transaction_history(
    monkeypatch: pytest.MonkeyPatch,
    make_dashboard_window: TransactionWindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    portfolio = summary("Missing price")
    asset = global_asset("NEEDS", "Needs a price")
    buy = transaction(asset.id, TransactionType.BUY)
    selected_details = details(
        portfolio,
        assets=(position_asset(asset),),
        transactions=(buy,),
    )
    portfolio_service = StatefulPortfolioService((portfolio,), (selected_details,))
    price_service = StatefulMarketPriceService()
    dashboard_service = StatefulDashboardService(portfolio_service)
    dashboard_service.error = MissingMarketPriceError(asset.id)

    window = make_dashboard_window(
        portfolio_service,
        StatefulAssetService((asset,)),
        price_service,
        dashboard_service,
    )

    assert errors == []
    assert window._portfolio_dashboard is None
    assert window.findChild(QLabel, "valuationState").text() == (
        "Valuation unavailable. Record a current price for NEEDS."
    )
    assert window.findChild(QTableWidget, "positionTable").rowCount() == 0
    assert window.findChild(QTableWidget, "currencyValuationTable").rowCount() == 0
    assert window.findChild(QTableWidget, "transactionTable").rowCount() == 1
    assert price_service.latest_calls == []
    assert "record a current Asset price" in window.statusBar().currentMessage()


def test_missing_price_unknown_asset_uses_uuid_fallback_on_manual_refresh(
    monkeypatch: pytest.MonkeyPatch,
    make_dashboard_window: TransactionWindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    portfolio = summary("Unknown price Asset")
    selected_details = details(portfolio)
    portfolio_service = StatefulPortfolioService((portfolio,), (selected_details,))
    dashboard_service = StatefulDashboardService(portfolio_service)
    window = make_dashboard_window(
        portfolio_service,
        StatefulAssetService(),
        StatefulMarketPriceService(),
        dashboard_service,
    )
    missing_asset_id = uuid4()
    dashboard_service.queued_results.append(MissingMarketPriceError(missing_asset_id))

    window.findChild(QPushButton, "refreshValuationButton").click()

    assert errors == []
    assert len(dashboard_service.calls) == 2
    assert str(missing_asset_id) in window.findChild(QLabel, "valuationState").text()
    assert window._portfolio_dashboard is None


def test_calculation_error_clears_stale_dashboard_but_keeps_transactions(
    monkeypatch: pytest.MonkeyPatch,
    make_dashboard_window: TransactionWindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    portfolio = summary("Invalid persisted state")
    asset = global_asset("BROKEN", "Broken")
    buy = transaction(asset.id, TransactionType.BUY)
    selected_details = details(
        portfolio,
        assets=(position_asset(asset),),
        transactions=(buy,),
    )
    portfolio_service = StatefulPortfolioService((portfolio,), (selected_details,))
    dashboard_service = StatefulDashboardService(portfolio_service)
    dashboard_service.results_by_id[portfolio.id] = PortfolioDashboard(
        portfolio=selected_details,
        positions=(valued_position(asset.symbol, asset.name, asset_id=asset.id),),
        currencies=(),
    )
    window = make_dashboard_window(
        portfolio_service,
        StatefulAssetService((asset,)),
        StatefulMarketPriceService(),
        dashboard_service,
    )
    dashboard_service.queued_results.append(
        InsufficientPositionError(
            asset.id,
            Decimal("1.0000"),
            Decimal("2.0000"),
        )
    )

    window.findChild(QPushButton, "refreshValuationButton").click()

    assert len(errors) == 1
    assert errors[0][1] == "Unable to calculate valuation"
    assert "cannot be valued" in errors[0][2]
    assert window._portfolio_dashboard is None
    assert window.findChild(QTableWidget, "positionTable").rowCount() == 0
    assert window.findChild(QTableWidget, "currencyValuationTable").rowCount() == 0
    assert window.findChild(QTableWidget, "transactionTable").rowCount() == 1
    assert portfolio_service.delete_calls == []


def test_switch_clears_old_rows_and_uses_uuid_with_duplicate_portfolio_names(
    make_dashboard_window: TransactionWindowFactory,
) -> None:
    first = summary("Duplicate")
    second = summary("Duplicate")
    first_asset = global_asset("FIRST", "First")
    second_asset = global_asset("SECOND", "Second", currency=Currency.USD)
    first_details = details(first, assets=(position_asset(first_asset),))
    second_details = details(second, assets=(position_asset(second_asset),))
    portfolio_service = StatefulPortfolioService(
        (first, second),
        (first_details, second_details),
    )
    dashboard_service = StatefulDashboardService(portfolio_service)
    dashboard_service.results_by_id[first.id] = PortfolioDashboard(
        portfolio=first_details,
        positions=(valued_position("FIRST", "First", asset_id=first_asset.id),),
        currencies=(),
    )
    dashboard_service.results_by_id[second.id] = PortfolioDashboard(
        portfolio=second_details,
        positions=(
            valued_position(
                "SECOND",
                "Second",
                asset_id=second_asset.id,
                currency=Currency.USD,
            ),
        ),
        currencies=(),
    )
    window = make_dashboard_window(
        portfolio_service,
        StatefulAssetService((first_asset, second_asset)),
        StatefulMarketPriceService(),
        dashboard_service,
    )
    table = window.findChild(QTableWidget, "positionTable")
    rows_seen_before_second_query: list[int] = []

    def inspect_cleared_state(query: GetPortfolioDashboardQuery) -> None:
        if query.portfolio_id == second.id:
            rows_seen_before_second_query.append(table.rowCount())

    dashboard_service.before_get = inspect_cleared_state
    window.findChild(QComboBox, "portfolioSelector").setCurrentIndex(1)

    assert rows_seen_before_second_query == [0]
    assert dashboard_service.calls == [
        GetPortfolioDashboardQuery(portfolio_id=first.id),
        GetPortfolioDashboardQuery(portfolio_id=second.id),
    ]
    assert window._selected_portfolio_id == second.id
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "SECOND — Second"
    assert table.item(0, 0).data(Qt.ItemDataRole.UserRole) == str(second_asset.id)


def test_manual_refresh_replaces_dashboard_without_other_service_calls(
    make_dashboard_window: TransactionWindowFactory,
) -> None:
    portfolio = summary("Manual refresh")
    asset = global_asset("VALUE", "Valued")
    selected_details = details(portfolio, assets=(position_asset(asset),))
    portfolio_service = StatefulPortfolioService((portfolio,), (selected_details,))
    asset_service = StatefulAssetService((asset,))
    price_service = StatefulMarketPriceService()
    dashboard_service = StatefulDashboardService(portfolio_service)
    initial = PortfolioDashboard(
        portfolio=selected_details,
        positions=(
            valued_position(
                asset.symbol,
                asset.name,
                asset_id=asset.id,
                market_value="1.0000",
            ),
        ),
        currencies=(),
    )
    refreshed = PortfolioDashboard(
        portfolio=selected_details,
        positions=(
            valued_position(
                asset.symbol,
                asset.name,
                asset_id=asset.id,
                market_value="999.0000",
            ),
        ),
        currencies=(),
    )
    dashboard_service.results_by_id[portfolio.id] = initial
    window = make_dashboard_window(
        portfolio_service,
        asset_service,
        price_service,
        dashboard_service,
    )
    dashboard_service.results_by_id[portfolio.id] = refreshed

    window.findChild(QPushButton, "refreshValuationButton").click()

    assert len(dashboard_service.calls) == 2
    assert window._portfolio_dashboard is refreshed
    assert window.findChild(QTableWidget, "positionTable").item(0, 8).text() == "999.0000"
    assert portfolio_service.get_calls == [GetPortfolioQuery(portfolio_id=portfolio.id)]
    assert portfolio_service.list_calls == [ListPortfoliosQuery()]
    assert asset_service.list_calls == [ListAssetsQuery()]
    assert portfolio_service.buy_calls == []
    assert portfolio_service.sell_calls == []
    assert portfolio_service.delete_calls == []
    assert price_service.record_calls == []
    assert price_service.latest_calls == []
    assert window.statusBar().currentMessage() == "Valuation refreshed"


def test_asset_creation_alone_retains_confirmed_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    make_dashboard_window: TransactionWindowFactory,
) -> None:
    portfolio = summary("Asset creation boundary")
    selected_details = details(portfolio)
    portfolio_service = StatefulPortfolioService((portfolio,), (selected_details,))
    existing = global_asset("OLD", "Old")
    created = global_asset(
        "NEW",
        "New",
        currency=Currency.USD,
    )
    asset_service = StatefulAssetService((existing,))
    asset_service.create_result = created
    dashboard_service = StatefulDashboardService(portfolio_service)
    confirmed = PortfolioDashboard(
        portfolio=selected_details,
        positions=(),
        currencies=(),
    )
    dashboard_service.results_by_id[portfolio.id] = confirmed
    window = make_dashboard_window(
        portfolio_service,
        asset_service,
        StatefulMarketPriceService(),
        dashboard_service,
    )

    class AcceptedAssetDialog:
        def __init__(self, parent: MainWindow) -> None:
            assert parent is window

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def symbol(self) -> str:
            return created.symbol

        def asset_name(self) -> str:
            return created.name

        def asset_type(self) -> AssetType:
            return created.asset_type

        def currency(self) -> Currency:
            return created.currency

    monkeypatch.setattr(main_window_module, "CreateAssetDialog", AcceptedAssetDialog)
    window._create_asset()

    assert len(dashboard_service.calls) == 1
    assert window._portfolio_dashboard is confirmed
    assert len(asset_service.create_calls) == 1
    assert len(asset_service.list_calls) == 2


def test_price_write_without_portfolio_does_not_query_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    make_dashboard_window: TransactionWindowFactory,
) -> None:
    asset = global_asset("GLOBAL", "Global only", currency=Currency.GBP)
    portfolio_service = StatefulPortfolioService()
    asset_service = StatefulAssetService((asset,))
    price_service = StatefulMarketPriceService()
    price_service.record_result = MarketPriceView(
        id=uuid4(),
        asset_id=asset.id,
        price=Decimal("0.0000"),
        currency=asset.currency,
        observed_at=PRICE_AT,
    )
    dashboard_service = StatefulDashboardService(portfolio_service)
    window = make_dashboard_window(
        portfolio_service,
        asset_service,
        price_service,
        dashboard_service,
    )
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

    assert len(price_service.record_calls) == 1
    assert price_service.latest_calls == []
    assert dashboard_service.calls == []
    assert portfolio_service.get_calls == []
    assert window._portfolio_dashboard is None
