"""Focused tests for Desktop Alpha Portfolio and Asset management."""

from collections.abc import Iterator
from datetime import UTC, datetime
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
    QTableWidget,
)

from app.application.commands import CreateAssetCommand, CreatePortfolioCommand
from app.application.exceptions import ValidationError
from app.application.queries import (
    GetPortfolioDashboardQuery,
    GetPortfolioQuery,
    ListAssetsQuery,
    ListPortfoliosQuery,
)
from app.application.results import (
    AssetView,
    MarketPriceView,
    PortfolioDashboard,
    PortfolioDetails,
    PortfolioSummary,
)
from app.core.container import Container
from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency
from app.ui.windows import main_window as main_window_module
from app.ui.windows.main_window import MainWindow

CREATED_AT = datetime(2026, 7, 18, 9, 30, tzinfo=UTC)


def portfolio_summary(
    name: str,
    *,
    portfolio_id: UUID | None = None,
    currency: Currency = Currency.TRY,
    archived: bool = False,
) -> PortfolioSummary:
    """Build a stable Portfolio DTO for UI tests."""
    return PortfolioSummary(
        id=portfolio_id or uuid4(),
        name=name,
        base_currency=currency,
        is_archived=archived,
        created_at=CREATED_AT,
    )


def portfolio_details(summary: PortfolioSummary) -> PortfolioDetails:
    """Build the creation result matching a summary."""
    return PortfolioDetails(
        id=summary.id,
        name=summary.name,
        base_currency=summary.base_currency,
        assets=(),
        transactions=(),
        is_archived=summary.is_archived,
        created_at=summary.created_at,
    )


def asset_view(
    symbol: str,
    name: str,
    *,
    asset_id: UUID | None = None,
    asset_type: AssetType = AssetType.EQUITY,
    currency: Currency = Currency.TRY,
    active: bool = True,
) -> AssetView:
    """Build a stable global Asset DTO for UI tests."""
    return AssetView(
        id=asset_id or uuid4(),
        symbol=symbol,
        name=name,
        asset_type=asset_type,
        currency=currency,
        is_active=active,
        created_at=CREATED_AT,
    )


class FakePortfolioService:
    """Record only the management service calls used by MainWindow."""

    def __init__(self, portfolios: tuple[PortfolioSummary, ...] = ()) -> None:
        self.portfolios = portfolios
        self.details_by_id = {
            portfolio.id: portfolio_details(portfolio) for portfolio in portfolios
        }
        self.list_calls: list[ListPortfoliosQuery] = []
        self.get_calls: list[GetPortfolioQuery] = []
        self.create_calls: list[CreatePortfolioCommand] = []
        self.list_error: Exception | None = None
        self.get_error: Exception | None = None
        self.create_error: Exception | None = None
        self.create_result: PortfolioDetails | None = None

    def list_portfolios(
        self,
        query: ListPortfoliosQuery,
    ) -> tuple[PortfolioSummary, ...]:
        self.list_calls.append(query)
        if self.list_error is not None:
            raise self.list_error
        return self.portfolios

    def create_portfolio(self, command: CreatePortfolioCommand) -> PortfolioDetails:
        self.create_calls.append(command)
        if self.create_error is not None:
            raise self.create_error
        if self.create_result is None:
            raise AssertionError("The test must supply a Portfolio creation result.")
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

    def get_portfolio(self, query: GetPortfolioQuery) -> PortfolioDetails:
        self.get_calls.append(query)
        if self.get_error is not None:
            raise self.get_error
        return self.details_by_id[query.portfolio_id]


class FakeAssetService:
    """Record only the global Asset management calls used by MainWindow."""

    def __init__(self, assets: tuple[AssetView, ...] = ()) -> None:
        self.assets = assets
        self.list_calls: list[ListAssetsQuery] = []
        self.create_calls: list[CreateAssetCommand] = []
        self.list_error: Exception | None = None
        self.create_error: Exception | None = None
        self.create_result: AssetView | None = None

    def list_assets(self, query: ListAssetsQuery) -> tuple[AssetView, ...]:
        self.list_calls.append(query)
        if self.list_error is not None:
            raise self.list_error
        return self.assets

    def create_asset(self, command: CreateAssetCommand) -> AssetView:
        self.create_calls.append(command)
        if self.create_error is not None:
            raise self.create_error
        if self.create_result is None:
            raise AssertionError("The test must supply an Asset creation result.")
        self.assets = (*self.assets, self.create_result)
        return self.create_result


class FakeMarketPriceService:
    """Fail if Commit 7 management tests unexpectedly record or read prices."""

    def __init__(self) -> None:
        self.record_calls: list[object] = []
        self.latest_calls: list[object] = []
        self.record_result: MarketPriceView | None = None


class FakeDashboardService:
    """Return empty calculated dashboards around fake persisted details."""

    def __init__(self, portfolios: FakePortfolioService) -> None:
        self._portfolios = portfolios
        self.calls: list[GetPortfolioDashboardQuery] = []

    def get_dashboard(self, query: GetPortfolioDashboardQuery) -> PortfolioDashboard:
        self.calls.append(query)
        return PortfolioDashboard(
            portfolio=self._portfolios.details_by_id[query.portfolio_id],
            positions=(),
            currencies=(),
        )


class WindowFactory:
    """Construct and clean up MainWindows around lightweight services."""

    def __init__(self, application: QApplication) -> None:
        self.application = application
        self.windows: list[MainWindow] = []

    def __call__(
        self,
        portfolios: FakePortfolioService,
        assets: FakeAssetService,
        market_prices: FakeMarketPriceService | None = None,
        dashboards: FakeDashboardService | None = None,
    ) -> MainWindow:
        price_service = market_prices or FakeMarketPriceService()
        dashboard_service = dashboards or FakeDashboardService(portfolios)
        container = cast(
            Container,
            SimpleNamespace(
                settings=SimpleNamespace(app_name="Mira UI Test"),
                portfolio_application_service=portfolios,
                asset_application_service=assets,
                market_price_application_service=price_service,
                portfolio_dashboard_query_service=dashboard_service,
            ),
        )
        window = MainWindow(container)
        self.windows.append(window)
        return window

    def close_all(self) -> None:
        """Release widgets and process deferred Qt events."""
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.application.processEvents()


@pytest.fixture
def make_window(qapplication: QApplication) -> Iterator[WindowFactory]:
    """Yield a tracked MainWindow factory."""
    factory = WindowFactory(qapplication)
    yield factory
    factory.close_all()


def capture_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[QMainWindow, str, str]]:
    """Replace modal critical dialogs with an assertion-friendly recorder."""
    errors: list[tuple[QMainWindow, str, str]] = []

    def record(parent: QMainWindow, title: str, message: str) -> None:
        errors.append((parent, title, message))

    monkeypatch.setattr(QMessageBox, "critical", record)
    return errors


def test_initial_load_populates_ordered_uuid_backed_management_state(
    make_window: WindowFactory,
) -> None:
    first_id = uuid4()
    second_id = uuid4()
    first = portfolio_summary("Shared name", portfolio_id=first_id)
    second = portfolio_summary(
        "Shared name",
        portfolio_id=second_id,
        currency=Currency.USD,
        archived=True,
    )
    first_asset = asset_view("DUP", "First instrument")
    second_asset = asset_view(
        "DUP",
        "Second instrument",
        asset_type=AssetType.ETF,
        currency=Currency.EUR,
        active=False,
    )
    portfolio_service = FakePortfolioService((first, second))
    asset_service = FakeAssetService((first_asset, second_asset))

    window = make_window(portfolio_service, asset_service)
    selector = window.findChild(QComboBox, "portfolioSelector")
    table = window.findChild(QTableWidget, "assetRegistryTable")
    details = window.findChild(QLabel, "selectedPortfolioDetails")

    assert len(portfolio_service.list_calls) == 1
    assert portfolio_service.get_calls == [GetPortfolioQuery(portfolio_id=first_id)]
    assert len(asset_service.list_calls) == 1
    assert portfolio_service.create_calls == []
    assert asset_service.create_calls == []
    assert selector is not None
    assert [selector.itemText(index) for index in range(selector.count())] == [
        "Shared name",
        "Shared name",
    ]
    assert [selector.itemData(index) for index in range(selector.count())] == [
        str(first_id),
        str(second_id),
    ]
    assert window._selected_portfolio_id == first_id
    assert details is not None
    assert "TRY" in details.text()
    assert "2026-07-18 09:30" in details.text()
    assert "Active" in details.text()

    selector.setCurrentIndex(1)
    assert portfolio_service.get_calls == [
        GetPortfolioQuery(portfolio_id=first_id),
        GetPortfolioQuery(portfolio_id=second_id),
    ]
    assert window._selected_portfolio_id == second_id
    assert "USD" in details.text()
    assert "Archived" in details.text()

    assert table is not None
    assert table.rowCount() == 2
    assert table.columnCount() == 5
    assert [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())] == [
        "Symbol",
        "Name",
        "Type",
        "Currency",
        "Status",
    ]
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == [
        "DUP",
        "DUP",
    ]
    assert table.item(0, 0).data(Qt.ItemDataRole.UserRole) == str(first_asset.id)
    assert table.item(1, 0).data(Qt.ItemDataRole.UserRole) == str(second_asset.id)
    assert table.item(0, 0).data(Qt.ItemDataRole.UserRole) != table.item(1, 0).data(
        Qt.ItemDataRole.UserRole
    )
    assert table.item(1, 2).text() == "ETF"
    assert table.item(1, 3).text() == "EUR"
    assert table.item(1, 4).text() == "Inactive"
    assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert window.statusBar().currentMessage() == "Ready"


def test_empty_state_disables_selector_without_placeholder_rows(
    make_window: WindowFactory,
) -> None:
    window = make_window(FakePortfolioService(), FakeAssetService())
    selector = window.findChild(QComboBox, "portfolioSelector")
    table = window.findChild(QTableWidget, "assetRegistryTable")
    portfolio_empty = window.findChild(QLabel, "portfolioEmptyState")
    asset_empty = window.findChild(QLabel, "assetEmptyState")

    assert selector is not None
    assert not selector.isEnabled()
    assert selector.count() == 0
    assert window._selected_portfolio_id is None
    assert window._selected_portfolio_details is None
    assert table is not None
    assert table.rowCount() == 0
    assert portfolio_empty is not None
    assert "Create a portfolio" in portfolio_empty.text()
    assert portfolio_empty.isVisibleTo(window)
    assert asset_empty is not None
    assert asset_empty.text() == "No assets have been created yet."
    assert asset_empty.isVisibleTo(window)
    assert window.findChild(QLabel, "valuationState").text() == (
        "Create or select a portfolio to view valuation."
    )
    assert window.findChild(QLabel, "selectedPortfolioName").text() == ("No portfolio selected")
    assert window.findChild(type(window._new_portfolio_button), "newPortfolioButton").isEnabled()


def test_portfolio_creation_refreshes_once_and_selects_returned_uuid(
    monkeypatch: pytest.MonkeyPatch,
    make_window: WindowFactory,
) -> None:
    existing = portfolio_summary("Existing")
    created_summary = portfolio_summary("New portfolio", currency=Currency.GBP)
    portfolio_service = FakePortfolioService((existing,))
    portfolio_service.create_result = portfolio_details(created_summary)
    asset_service = FakeAssetService()
    dashboard_service = FakeDashboardService(portfolio_service)
    window = make_window(
        portfolio_service,
        asset_service,
        dashboards=dashboard_service,
    )

    class AcceptedPortfolioDialog:
        def __init__(self, parent: MainWindow) -> None:
            assert parent is window

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def portfolio_name(self) -> str:
            return "  New portfolio  "

    monkeypatch.setattr(
        main_window_module,
        "CreatePortfolioDialog",
        AcceptedPortfolioDialog,
    )

    window._create_portfolio()

    assert portfolio_service.create_calls == [
        CreatePortfolioCommand(portfolio_name="  New portfolio  ")
    ]
    assert len(portfolio_service.list_calls) == 2
    assert portfolio_service.get_calls == [
        GetPortfolioQuery(portfolio_id=existing.id),
        GetPortfolioQuery(portfolio_id=created_summary.id),
    ]
    assert dashboard_service.calls == [
        GetPortfolioDashboardQuery(portfolio_id=existing.id),
        GetPortfolioDashboardQuery(portfolio_id=created_summary.id),
    ]
    assert len(asset_service.list_calls) == 1
    assert asset_service.create_calls == []
    assert window._selected_portfolio_id == created_summary.id
    assert window.findChild(QLabel, "selectedPortfolioName").text() == "New portfolio"
    assert "GBP" in window.findChild(QLabel, "selectedPortfolioDetails").text()
    assert window.statusBar().currentMessage() == "Portfolio created"


def test_portfolio_cancel_and_failure_preserve_selection_without_refresh(
    monkeypatch: pytest.MonkeyPatch,
    make_window: WindowFactory,
) -> None:
    first = portfolio_summary("First")
    second = portfolio_summary("Second")
    portfolio_service = FakePortfolioService((first, second))
    asset_service = FakeAssetService()
    window = make_window(portfolio_service, asset_service)
    selector = window.findChild(QComboBox, "portfolioSelector")
    assert selector is not None
    selector.setCurrentIndex(1)

    class RejectedPortfolioDialog:
        def __init__(self, parent: MainWindow) -> None:
            assert parent is window

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(
        main_window_module,
        "CreatePortfolioDialog",
        RejectedPortfolioDialog,
    )
    window._create_portfolio()
    assert portfolio_service.create_calls == []
    assert len(portfolio_service.list_calls) == 1
    assert window._selected_portfolio_id == second.id
    assert portfolio_service.get_calls == [
        GetPortfolioQuery(portfolio_id=first.id),
        GetPortfolioQuery(portfolio_id=second.id),
    ]

    errors = capture_errors(monkeypatch)
    portfolio_service.create_error = ValidationError("Name is required.")

    class AcceptedPortfolioDialog(RejectedPortfolioDialog):
        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def portfolio_name(self) -> str:
            return ""

    monkeypatch.setattr(
        main_window_module,
        "CreatePortfolioDialog",
        AcceptedPortfolioDialog,
    )
    window._create_portfolio()

    assert portfolio_service.create_calls == [CreatePortfolioCommand(portfolio_name="")]
    assert len(portfolio_service.list_calls) == 1
    assert len(asset_service.list_calls) == 1
    assert asset_service.create_calls == []
    assert window._selected_portfolio_id == second.id
    assert selector.count() == 2
    assert len(errors) == 1
    assert errors[0][1] == "Unable to create portfolio"
    assert "Name is required." in errors[0][2]
    assert window.statusBar().currentMessage() == "Unable to create portfolio"


def test_asset_creation_refreshes_once_and_targets_uuid_not_symbol(
    monkeypatch: pytest.MonkeyPatch,
    make_window: WindowFactory,
) -> None:
    existing = asset_view("DUP", "Existing")
    created = asset_view(
        "DUP",
        "New duplicate",
        asset_type=AssetType.CRYPTO,
        currency=Currency.USD,
    )
    portfolio_service = FakePortfolioService()
    asset_service = FakeAssetService((existing,))
    asset_service.create_result = created
    window = make_window(portfolio_service, asset_service)

    class AcceptedAssetDialog:
        def __init__(self, parent: MainWindow) -> None:
            assert parent is window

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def symbol(self) -> str:
            return "DUP"

        def asset_name(self) -> str:
            return "New duplicate"

        def asset_type(self) -> AssetType:
            return AssetType.CRYPTO

        def currency(self) -> Currency:
            return Currency.USD

    monkeypatch.setattr(main_window_module, "CreateAssetDialog", AcceptedAssetDialog)
    window._create_asset()

    assert asset_service.create_calls == [
        CreateAssetCommand(
            symbol="DUP",
            name="New duplicate",
            asset_type=AssetType.CRYPTO,
            currency=Currency.USD,
        )
    ]
    assert len(asset_service.list_calls) == 2
    assert len(portfolio_service.list_calls) == 1
    assert portfolio_service.create_calls == []
    table = window.findChild(QTableWidget, "assetRegistryTable")
    assert table is not None
    assert table.rowCount() == 2
    assert window._selected_asset_id == created.id
    assert table.currentRow() == 1
    assert table.item(1, 0).data(Qt.ItemDataRole.UserRole) == str(created.id)
    assert window.statusBar().currentMessage() == "Asset created"


def test_asset_cancel_and_failure_leave_existing_rows_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    make_window: WindowFactory,
) -> None:
    existing = asset_view("SAFE", "Existing")
    portfolio_service = FakePortfolioService()
    asset_service = FakeAssetService((existing,))
    window = make_window(portfolio_service, asset_service)

    class RejectedAssetDialog:
        def __init__(self, parent: MainWindow) -> None:
            assert parent is window

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(main_window_module, "CreateAssetDialog", RejectedAssetDialog)
    window._create_asset()
    assert asset_service.create_calls == []
    assert len(asset_service.list_calls) == 1

    errors = capture_errors(monkeypatch)
    asset_service.create_error = ValidationError("Symbol is required.")

    class AcceptedAssetDialog(RejectedAssetDialog):
        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def symbol(self) -> str:
            return ""

        def asset_name(self) -> str:
            return "Invalid"

        def asset_type(self) -> AssetType:
            return AssetType.EQUITY

        def currency(self) -> Currency:
            return Currency.TRY

    monkeypatch.setattr(main_window_module, "CreateAssetDialog", AcceptedAssetDialog)
    window._create_asset()

    table = window.findChild(QTableWidget, "assetRegistryTable")
    assert asset_service.create_calls == [
        CreateAssetCommand(
            symbol="",
            name="Invalid",
            asset_type=AssetType.EQUITY,
            currency=Currency.TRY,
        )
    ]
    assert len(asset_service.list_calls) == 1
    assert len(portfolio_service.list_calls) == 1
    assert portfolio_service.create_calls == []
    assert table is not None
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "SAFE"
    assert len(errors) == 1
    assert errors[0][1] == "Unable to create asset"
    assert "Symbol is required." in errors[0][2]
    assert window.statusBar().currentMessage() == "Unable to create asset"


def test_refresh_preserves_uuid_selection_and_uses_truthful_fallbacks(
    make_window: WindowFactory,
) -> None:
    first = portfolio_summary("Duplicate")
    second = portfolio_summary("Duplicate")
    first_asset = asset_view("DUP", "First")
    second_asset = asset_view("DUP", "Second")
    portfolio_service = FakePortfolioService((first, second))
    asset_service = FakeAssetService((first_asset, second_asset))
    window = make_window(portfolio_service, asset_service)
    selector = window.findChild(QComboBox, "portfolioSelector")
    table = window.findChild(QTableWidget, "assetRegistryTable")
    assert selector is not None
    assert table is not None

    selector.setCurrentIndex(1)
    table.selectRow(1)
    assert window._selected_portfolio_id == second.id
    assert window._selected_asset_id == second_asset.id

    assert window._refresh_portfolios()
    assert window._refresh_assets()
    assert portfolio_service.get_calls == [
        GetPortfolioQuery(portfolio_id=first.id),
        GetPortfolioQuery(portfolio_id=second.id),
    ]
    assert window._selected_portfolio_id == second.id
    assert selector.currentData() == str(second.id)
    assert window._selected_asset_id == second_asset.id
    assert table.currentRow() == 1

    portfolio_service.portfolios = (first,)
    asset_service.assets = (first_asset,)
    assert window._refresh_portfolios()
    assert window._refresh_assets()
    assert portfolio_service.get_calls[-1] == GetPortfolioQuery(portfolio_id=first.id)
    assert window._selected_portfolio_id == first.id
    assert selector.currentData() == str(first.id)
    assert window._selected_asset_id is None

    portfolio_service.portfolios = ()
    assert window._refresh_portfolios()
    assert window._selected_portfolio_id is None
    assert not selector.isEnabled()


def test_portfolio_load_failure_does_not_block_asset_load(
    monkeypatch: pytest.MonkeyPatch,
    make_window: WindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    portfolio_service = FakePortfolioService()
    portfolio_service.list_error = ValidationError("Portfolio read failed.")
    asset = asset_view("SAFE", "Still visible")
    asset_service = FakeAssetService((asset,))

    window = make_window(portfolio_service, asset_service)

    assert len(portfolio_service.list_calls) == 1
    assert len(asset_service.list_calls) == 1
    assert window.findChild(QComboBox, "portfolioSelector").count() == 0
    assert window.findChild(QTableWidget, "assetRegistryTable").rowCount() == 1
    assert window.findChild(QLabel, "portfolioEmptyState").text() == ("Unable to load portfolios.")
    assert len(errors) == 1
    assert errors[0][1] == "Unable to load portfolios"
    assert window.statusBar().currentMessage() == "Unable to load portfolios"


def test_asset_load_failure_does_not_block_portfolio_load(
    monkeypatch: pytest.MonkeyPatch,
    make_window: WindowFactory,
) -> None:
    errors = capture_errors(monkeypatch)
    portfolio = portfolio_summary("Still visible")
    portfolio_service = FakePortfolioService((portfolio,))
    asset_service = FakeAssetService()
    asset_service.list_error = ValidationError("Asset read failed.")

    window = make_window(portfolio_service, asset_service)

    assert len(portfolio_service.list_calls) == 1
    assert len(asset_service.list_calls) == 1
    assert window.findChild(QComboBox, "portfolioSelector").count() == 1
    assert window._selected_portfolio_id == portfolio.id
    assert window.findChild(QTableWidget, "assetRegistryTable").rowCount() == 0
    assert window.findChild(QLabel, "assetEmptyState").text() == "Unable to load assets."
    assert len(errors) == 1
    assert errors[0][1] == "Unable to load assets"
    assert window.statusBar().currentMessage() == "Unable to load assets"


def test_main_window_smoke_and_visible_text_are_truthful(
    make_window: WindowFactory,
) -> None:
    window = make_window(FakePortfolioService(), FakeAssetService())
    visible_text = " ".join(label.text() for label in window.findChildren(QLabel))

    assert window.windowTitle() == "Mira UI Test"
    assert window.minimumWidth() >= 1080
    assert window.minimumHeight() >= 700
    assert "Portfolio setup" in visible_text
    assert "Asset registry" in visible_text
    assert "Calculated valuation" in visible_text
    for fabricated in (
        "THYAO",
        "TUPRS",
        "AAPL",
        "1,284,650",
        "10,452.18",
        "Market data refreshes automatically",
        "Total value",
        "P&L",
        "Portfolio allocation",
        "Market pulse",
    ):
        assert fabricated not in visible_text

    window.close()
    assert not window.isVisible()


def test_ui_source_respects_management_service_boundary() -> None:
    source = Path(main_window_module.__file__).read_text(encoding="utf-8")

    for forbidden_import in (
        "app.infrastructure",
        "sqlalchemy",
        "Session",
        "app.repositories",
        "UnitOfWork",
        "DatabaseManager",
    ):
        assert forbidden_import not in source
    for forbidden_container_access in (
        "container.session_factory",
        "container.unit_of_work_factory",
        "container.database_manager",
    ):
        assert forbidden_container_access not in source
    for forbidden_workflow in ("GetLatestMarketPriceQuery",):
        assert forbidden_workflow not in source
    for forbidden_demo in (
        "THYAO",
        "TUPRS",
        "AAPL",
        "1,284,650",
        "10,452.18",
        "Market data refreshes automatically",
    ):
        assert forbidden_demo not in source
    for forbidden_implementation in (
        "float(",
        "datetime.now(",
        "select(",
        "text(",
        "QTimer",
        "threading",
        "async def",
        "except Exception: pass",
    ):
        assert forbidden_implementation not in source
