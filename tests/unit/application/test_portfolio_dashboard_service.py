"""Focused tests for the calculated Portfolio dashboard application query."""

import ast
import inspect
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Literal, Self, get_type_hints
from uuid import UUID

import pytest

import app.application as application
import app.application.services as services_module
from app.application.exceptions import PortfolioNotFoundError
from app.application.ports import AssetRepository, SnapshotRepository
from app.application.queries import GetPortfolioDashboardQuery
from app.application.results import (
    CurrencyValuationView,
    PortfolioDashboard,
    ValuedAssetPositionView,
)
from app.application.services import (
    DefaultPortfolioDashboardQueryService,
    PortfolioDashboardQueryService,
)
from app.application.unit_of_work import UnitOfWork
from app.core.exceptions import RepositoryError
from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.price_history import PriceHistory
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.exceptions import (
    InsufficientPositionError,
    MissingMarketPriceError,
    PositionAssetNotFoundError,
    UnsupportedTransactionTypeError,
)
from app.domain.services import (
    PortfolioPositionCalculator,
    PortfolioValuationCalculator,
)
from app.domain.value_objects.asset_position import AssetPosition
from app.domain.value_objects.currency import Currency
from app.domain.value_objects.portfolio_valuation import (
    PortfolioValuation,
    ValuedAssetPosition,
)

NOW = datetime(2026, 7, 21, 10, 15, 30, 123456, tzinfo=UTC)
LATER = datetime(2026, 7, 21, 14, 45, 12, 654321, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICES_PATH = PROJECT_ROOT / "app/application/services.py"


class FakePortfolioRepository:
    """Record Portfolio repository calls while enforcing an active UnitOfWork."""

    def __init__(
        self,
        items: dict[UUID, Portfolio],
        is_active: Callable[[], bool],
        events: list[str],
    ) -> None:
        self.items = items
        self._is_active = is_active
        self._events = events
        self.added: list[Portfolio] = []
        self.saved: list[Portfolio] = []
        self.get_calls: list[UUID] = []
        self.list_calls: list[tuple[int, int]] = []
        self.deleted: list[UUID] = []
        self.exists_calls: list[UUID] = []
        self.count_calls = 0

    def _require_active(self) -> None:
        if not self._is_active():
            raise RuntimeError("Repository accessed outside its Unit of Work.")

    def add(self, portfolio: Portfolio) -> Portfolio:
        self._require_active()
        self.added.append(portfolio)
        self.items[portfolio.id] = portfolio
        return portfolio

    def save(self, portfolio: Portfolio) -> Portfolio:
        self._require_active()
        self.saved.append(portfolio)
        self.items[portfolio.id] = portfolio
        return portfolio

    def get(self, portfolio_id: UUID) -> Portfolio | None:
        self._require_active()
        self._events.append("portfolio.get")
        self.get_calls.append(portfolio_id)
        return self.items.get(portfolio_id)

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Portfolio]:
        self._require_active()
        self.list_calls.append((offset, limit))
        return list(self.items.values())[offset : offset + limit]

    def delete(self, portfolio_id: UUID) -> None:
        self._require_active()
        self.deleted.append(portfolio_id)
        self.items.pop(portfolio_id, None)

    def exists(self, portfolio_id: UUID) -> bool:
        self._require_active()
        self.exists_calls.append(portfolio_id)
        return portfolio_id in self.items

    def count(self) -> int:
        self._require_active()
        self.count_calls += 1
        return len(self.items)


class FakePriceHistoryRepository:
    """Return controlled latest prices and record every repository operation."""

    def __init__(
        self,
        latest_by_asset: dict[UUID, PriceHistory | None],
        is_active: Callable[[], bool],
        events: list[str],
        latest_error: BaseException | None,
    ) -> None:
        self.latest_by_asset = latest_by_asset
        self._is_active = is_active
        self._events = events
        self._latest_error = latest_error
        self.added: list[PriceHistory] = []
        self.get_calls: list[UUID] = []
        self.latest_calls: list[UUID] = []
        self.list_calls: list[tuple[int, int]] = []
        self.deleted: list[UUID] = []
        self.exists_calls: list[UUID] = []
        self.count_calls = 0

    def _require_active(self) -> None:
        if not self._is_active():
            raise RuntimeError("Repository accessed outside its Unit of Work.")

    def add(self, price_history: PriceHistory) -> PriceHistory:
        self._require_active()
        self.added.append(price_history)
        return price_history

    def get(self, price_history_id: UUID) -> PriceHistory | None:
        self._require_active()
        self.get_calls.append(price_history_id)
        return None

    def get_latest_for_asset(self, asset_id: UUID) -> PriceHistory | None:
        self._require_active()
        self._events.append(f"price.latest:{asset_id}")
        self.latest_calls.append(asset_id)
        if self._latest_error is not None:
            raise self._latest_error
        return self.latest_by_asset.get(asset_id)

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[PriceHistory]:
        self._require_active()
        self.list_calls.append((offset, limit))
        return ()

    def delete(self, price_history: PriceHistory) -> None:
        self._require_active()
        self.deleted.append(price_history.id)

    def exists(self, price_history_id: UUID) -> bool:
        self._require_active()
        self.exists_calls.append(price_history_id)
        return False

    def count(self) -> int:
        self._require_active()
        self.count_calls += 1
        return 0


class TrackingUnitOfWork:
    """Expose only dashboard repositories and record transaction lifecycle."""

    def __init__(
        self,
        portfolios: dict[UUID, Portfolio],
        latest_by_asset: dict[UUID, PriceHistory | None],
        latest_error: BaseException | None,
    ) -> None:
        self.active = False
        self.enter_count = 0
        self.exit_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.exit_exceptions: list[type[BaseException] | None] = []
        self.events: list[str] = []
        self.portfolio_repository = FakePortfolioRepository(
            portfolios,
            lambda: self.active,
            self.events,
        )
        self.price_repository = FakePriceHistoryRepository(
            latest_by_asset,
            lambda: self.active,
            self.events,
            latest_error,
        )

    @property
    def assets(self) -> AssetRepository:
        self._require_active()
        raise AssertionError("Asset repository is outside the dashboard workflow.")

    @property
    def portfolios(self) -> FakePortfolioRepository:
        self._require_active()
        return self.portfolio_repository

    @property
    def price_history(self) -> FakePriceHistoryRepository:
        self._require_active()
        return self.price_repository

    @property
    def snapshots(self) -> SnapshotRepository:
        self._require_active()
        raise AssertionError("Snapshot repository is outside the dashboard workflow.")

    def __enter__(self) -> Self:
        if self.active:
            raise RuntimeError("Unit of Work is already active.")
        self.active = True
        self.enter_count += 1
        self.events.append("uow.enter")
        return self

    def commit(self) -> None:
        self._require_active()
        self.commit_count += 1
        self.events.append("uow.commit")

    def rollback(self) -> None:
        self._require_active()
        self.rollback_count += 1
        self.events.append("uow.rollback")

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exception, traceback
        self._require_active()
        self.exit_count += 1
        self.exit_exceptions.append(exception_type)
        self.events.append("uow.exit")
        self.active = False
        return False

    def _require_active(self) -> None:
        if not self.active:
            raise RuntimeError("Unit of Work is not active.")


class TrackingUnitOfWorkFactory:
    """Create one fresh read-only UnitOfWork per dashboard invocation."""

    def __init__(
        self,
        *,
        portfolios: Sequence[Portfolio] = (),
        latest_by_asset: dict[UUID, PriceHistory | None] | None = None,
        latest_error: BaseException | None = None,
    ) -> None:
        self.portfolio_state = {portfolio.id: portfolio for portfolio in portfolios}
        self.latest_by_asset = dict(latest_by_asset or {})
        self.latest_error = latest_error
        self.created: list[TrackingUnitOfWork] = []

    def __call__(self) -> TrackingUnitOfWork:
        unit_of_work = TrackingUnitOfWork(
            self.portfolio_state,
            self.latest_by_asset,
            self.latest_error,
        )
        self.created.append(unit_of_work)
        return unit_of_work


class RecordingPositionCalculator(PortfolioPositionCalculator):
    """Record calculator calls, optionally returning or raising controlled values."""

    def __init__(
        self,
        *,
        result: tuple[AssetPosition, ...] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[Portfolio] = []

    def calculate(self, portfolio: Portfolio) -> tuple[AssetPosition, ...]:
        self.calls.append(portfolio)
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return super().calculate(portfolio)


class RecordingValuationCalculator(PortfolioValuationCalculator):
    """Record market-price mappings, with optional controlled result or failure."""

    def __init__(
        self,
        *,
        result: PortfolioValuation | None = None,
        error: BaseException | None = None,
        position_calculator: PortfolioPositionCalculator | None = None,
    ) -> None:
        super().__init__(position_calculator=position_calculator)
        self.result = result
        self.error = error
        self.calls: list[tuple[Portfolio, dict[UUID, Decimal]]] = []

    def calculate(
        self,
        portfolio: Portfolio,
        market_prices: Mapping[UUID, Decimal],
    ) -> PortfolioValuation:
        self.calls.append((portfolio, dict(market_prices)))
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return super().calculate(portfolio, market_prices)


def make_asset(
    *,
    identity: int,
    symbol: str,
    currency: Currency = Currency.USD,
    asset_type: AssetType = AssetType.EQUITY,
) -> Asset:
    return Asset(
        id=UUID(int=identity),
        symbol=symbol,
        name=f"{symbol} Asset",
        asset_type=asset_type,
        currency=currency,
        created_at=NOW,
    )


def make_portfolio(
    *,
    identity: int,
    name: str,
    assets: Sequence[Asset] = (),
    base_currency: Currency = Currency.TRY,
    is_archived: bool = False,
) -> Portfolio:
    portfolio = Portfolio(
        id=UUID(int=identity),
        name=name,
        base_currency=base_currency,
        is_archived=is_archived,
        created_at=NOW,
    )
    for asset in assets:
        portfolio.add_asset(asset)
    return portfolio


def record_transaction(
    portfolio: Portfolio,
    asset: Asset,
    *,
    identity: int,
    quantity: str,
    price: str,
    transaction_type: TransactionType,
    commission: str = "0",
    tax: str = "0",
    date: datetime = NOW,
) -> Transaction:
    transaction = Transaction(
        id=UUID(int=identity),
        asset_id=asset.id,
        quantity=Decimal(quantity),
        price=Decimal(price),
        transaction_type=transaction_type,
        commission=Decimal(commission),
        tax=Decimal(tax),
        date=date,
    )
    portfolio.record_transaction(transaction)
    return transaction


def make_price(
    asset: Asset,
    *,
    identity: int,
    price: str,
    observed_at: datetime = LATER,
) -> PriceHistory:
    return PriceHistory(
        id=UUID(int=identity),
        asset_id=asset.id,
        price=Decimal(price),
        currency=asset.currency,
        observed_at=observed_at,
    )


def only_unit_of_work(factory: TrackingUnitOfWorkFactory) -> TrackingUnitOfWork:
    assert len(factory.created) == 1
    return factory.created[0]


def assert_read_only(unit_of_work: TrackingUnitOfWork) -> None:
    assert unit_of_work.commit_count == 0
    assert unit_of_work.rollback_count == 0
    assert unit_of_work.portfolio_repository.added == []
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.portfolio_repository.deleted == []
    assert unit_of_work.portfolio_repository.list_calls == []
    assert unit_of_work.portfolio_repository.exists_calls == []
    assert unit_of_work.portfolio_repository.count_calls == 0
    assert unit_of_work.price_repository.added == []
    assert unit_of_work.price_repository.get_calls == []
    assert unit_of_work.price_repository.list_calls == []
    assert unit_of_work.price_repository.deleted == []
    assert unit_of_work.price_repository.exists_calls == []
    assert unit_of_work.price_repository.count_calls == 0


def assert_dashboard_matches_domain(
    dashboard: PortfolioDashboard,
    portfolio: Portfolio,
    latest_prices: Mapping[UUID, PriceHistory],
) -> None:
    market_prices = {
        asset_id: price_history.price for asset_id, price_history in latest_prices.items()
    }
    expected = PortfolioValuationCalculator().calculate(portfolio, market_prices)
    assets_by_id = {asset.id: asset for asset in portfolio.assets}

    assert isinstance(dashboard.positions, tuple)
    assert isinstance(dashboard.currencies, tuple)
    assert len(dashboard.positions) == len(expected.positions)
    for view, valued_position in zip(
        dashboard.positions,
        expected.positions,
        strict=True,
    ):
        asset = assets_by_id[valued_position.asset_id]
        assert view == ValuedAssetPositionView(
            asset_id=valued_position.asset_id,
            symbol=asset.symbol,
            name=asset.name,
            asset_type=asset.asset_type,
            currency=valued_position.currency,
            quantity=valued_position.quantity,
            average_cost=valued_position.average_cost,
            cost_basis=valued_position.cost_basis,
            market_price=valued_position.market_price,
            price_observed_at=(
                latest_prices[valued_position.asset_id].observed_at
                if valued_position.market_price is not None
                else None
            ),
            market_value=valued_position.market_value,
            realized_pnl=valued_position.realized_pnl,
            unrealized_pnl=valued_position.unrealized_pnl,
            total_pnl=valued_position.total_pnl,
        )

    assert dashboard.currencies == tuple(
        CurrencyValuationView(
            currency=currency_valuation.currency,
            cost_basis=currency_valuation.cost_basis,
            market_value=currency_valuation.market_value,
            realized_pnl=currency_valuation.realized_pnl,
            unrealized_pnl=currency_valuation.unrealized_pnl,
            total_pnl=currency_valuation.total_pnl,
        )
        for currency_valuation in expected.currencies
    )


def test_default_dashboard_service_implements_contract_and_supports_injection() -> None:
    factory = TrackingUnitOfWorkFactory()
    position_calculator = RecordingPositionCalculator(result=())
    valuation_calculator = RecordingValuationCalculator(
        result=PortfolioValuation(positions=(), currencies=()),
    )
    service = DefaultPortfolioDashboardQueryService(
        factory,
        position_calculator=position_calculator,
        valuation_calculator=valuation_calculator,
    )

    assert isinstance(service, PortfolioDashboardQueryService)
    assert not inspect.isabstract(DefaultPortfolioDashboardQueryService)
    assert application.DefaultPortfolioDashboardQueryService is (
        DefaultPortfolioDashboardQueryService
    )
    assert tuple(inspect.signature(DefaultPortfolioDashboardQueryService.__init__).parameters) == (
        "self",
        "unit_of_work_factory",
        "position_calculator",
        "valuation_calculator",
    )
    assert get_type_hints(DefaultPortfolioDashboardQueryService.__init__) == {
        "unit_of_work_factory": Callable[[], UnitOfWork],
        "position_calculator": PortfolioPositionCalculator | None,
        "valuation_calculator": PortfolioValuationCalculator | None,
        "return": type(None),
    }
    assert get_type_hints(DefaultPortfolioDashboardQueryService.get_dashboard) == {
        "query": GetPortfolioDashboardQuery,
        "return": PortfolioDashboard,
    }
    assert not hasattr(application, "_to_portfolio_dashboard")


def test_empty_portfolio_returns_empty_dashboard_in_one_read_only_context() -> None:
    portfolio = make_portfolio(identity=1, name="Empty")
    factory = TrackingUnitOfWorkFactory(portfolios=(portfolio,))
    service = DefaultPortfolioDashboardQueryService(factory)

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert result.portfolio.id == portfolio.id
    assert result.portfolio.name == portfolio.name
    assert result.positions == ()
    assert result.currencies == ()
    assert unit_of_work.portfolio_repository.get_calls == [portfolio.id]
    assert unit_of_work.price_repository.latest_calls == []
    assert unit_of_work.enter_count == unit_of_work.exit_count == 1
    assert unit_of_work.events == ["uow.enter", "portfolio.get", "uow.exit"]
    assert_read_only(unit_of_work)


def test_unused_asset_remains_descriptive_without_position_or_price_lookup() -> None:
    unused = make_asset(identity=2, symbol="UNUSED", currency=Currency.EUR)
    portfolio = make_portfolio(
        identity=3,
        name="Unused Asset",
        assets=(unused,),
    )
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={unused.id: make_price(unused, identity=4, price="99")},
    )
    service = DefaultPortfolioDashboardQueryService(factory)

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert tuple(asset.id for asset in result.portfolio.assets) == (unused.id,)
    assert result.positions == ()
    assert result.currencies == ()
    assert unit_of_work.price_repository.latest_calls == []
    assert_read_only(unit_of_work)


def test_single_open_position_maps_exact_domain_values_metadata_and_timestamp() -> None:
    asset = make_asset(
        identity=10,
        symbol=" precise ",
        currency=Currency.GBP,
        asset_type=AssetType.ETF,
    )
    portfolio = make_portfolio(
        identity=11,
        name="  Precise Dashboard  ",
        assets=(asset,),
        base_currency=Currency.EUR,
        is_archived=True,
    )
    transaction = record_transaction(
        portfolio,
        asset,
        identity=12,
        quantity="1.2500",
        price="123456789.000000000123400",
        transaction_type=TransactionType.BUY,
        commission="1.2300",
        tax="0.4500",
    )
    latest = make_price(
        asset,
        identity=13,
        price="123456790.000000000123400",
        observed_at=LATER,
    )
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={asset.id: latest},
    )
    service = DefaultPortfolioDashboardQueryService(factory)
    assets_before = tuple(portfolio.assets)
    transactions_before = tuple(portfolio.transactions)

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert result.portfolio.id is portfolio.id
    assert result.portfolio.name == "Precise Dashboard"
    assert result.portfolio.base_currency is Currency.EUR
    assert result.portfolio.is_archived
    assert result.portfolio.created_at is NOW
    assert tuple(view.id for view in result.portfolio.assets) == (asset.id,)
    assert tuple(view.id for view in result.portfolio.transactions) == (transaction.id,)
    assert result.portfolio.transactions[0].commission is transaction.commission
    assert result.portfolio.transactions[0].tax is transaction.tax
    assert unit_of_work.price_repository.latest_calls == [asset.id]
    assert_dashboard_matches_domain(result, portfolio, {asset.id: latest})
    position = result.positions[0]
    assert position.symbol == "PRECISE"
    assert position.name == asset.name
    assert position.asset_type is AssetType.ETF
    assert position.currency is Currency.GBP
    assert position.price_observed_at is LATER
    assert position.price_observed_at.tzinfo is UTC
    assert position.price_observed_at.microsecond == 654321
    assert all(
        isinstance(value, Decimal)
        for value in (
            position.quantity,
            position.average_cost,
            position.cost_basis,
            position.market_price,
            position.market_value,
            position.realized_pnl,
            position.unrealized_pnl,
            position.total_pnl,
        )
    )
    assert tuple(portfolio.assets) == assets_before
    assert tuple(portfolio.transactions) == transactions_before
    assert_read_only(unit_of_work)


def test_zero_latest_price_is_consumed_without_fallback() -> None:
    asset = make_asset(identity=20, symbol="ZERO")
    portfolio = make_portfolio(identity=21, name="Zero", assets=(asset,))
    record_transaction(
        portfolio,
        asset,
        identity=22,
        quantity="2",
        price="25",
        transaction_type=TransactionType.BUY,
    )
    latest = make_price(asset, identity=23, price="0")
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={asset.id: latest},
    )
    service = DefaultPortfolioDashboardQueryService(factory)

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    position = result.positions[0]
    assert position.market_price == Decimal("0")
    assert position.market_price is not None
    assert position.price_observed_at is LATER
    assert position.market_value == Decimal("0")
    assert position.unrealized_pnl == -position.cost_basis
    assert only_unit_of_work(factory).price_repository.latest_calls == [asset.id]
    assert_read_only(only_unit_of_work(factory))


def test_partial_sell_preserves_domain_accounting_and_uses_one_latest_price() -> None:
    asset = make_asset(identity=30, symbol="PARTIAL")
    portfolio = make_portfolio(identity=31, name="Partial", assets=(asset,))
    record_transaction(
        portfolio,
        asset,
        identity=32,
        quantity="10",
        price="100",
        transaction_type=TransactionType.BUY,
        commission="10",
        tax="5",
    )
    record_transaction(
        portfolio,
        asset,
        identity=33,
        quantity="4",
        price="130",
        transaction_type=TransactionType.SELL,
        commission="5",
        tax="3",
        date=LATER,
    )
    latest = make_price(asset, identity=34, price="120")
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={asset.id: latest},
    )
    service = DefaultPortfolioDashboardQueryService(factory)

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    assert_dashboard_matches_domain(result, portfolio, {asset.id: latest})
    position = result.positions[0]
    expected = (
        PortfolioValuationCalculator()
        .calculate(
            portfolio,
            {asset.id: latest.price},
        )
        .positions[0]
    )
    assert position.quantity == expected.quantity
    assert position.average_cost == expected.average_cost
    assert position.cost_basis == expected.cost_basis
    assert position.realized_pnl == expected.realized_pnl
    assert position.unrealized_pnl == expected.unrealized_pnl
    assert position.total_pnl == expected.total_pnl
    assert only_unit_of_work(factory).price_repository.latest_calls == [asset.id]
    assert_read_only(only_unit_of_work(factory))


def test_closed_position_ignores_existing_price_and_retains_realized_result() -> None:
    asset = make_asset(identity=40, symbol="CLOSED")
    portfolio = make_portfolio(identity=41, name="Closed", assets=(asset,))
    record_transaction(
        portfolio,
        asset,
        identity=42,
        quantity="10",
        price="100",
        transaction_type=TransactionType.BUY,
    )
    record_transaction(
        portfolio,
        asset,
        identity=43,
        quantity="10",
        price="130",
        transaction_type=TransactionType.SELL,
        commission="4",
        tax="1",
        date=LATER,
    )
    ignored = make_price(asset, identity=44, price="999")
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={asset.id: ignored},
    )
    service = DefaultPortfolioDashboardQueryService(factory)
    assets_before = tuple(portfolio.assets)
    transactions_before = tuple(portfolio.transactions)

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    position = result.positions[0]
    assert unit_of_work.price_repository.latest_calls == []
    assert position.quantity == Decimal("0")
    assert position.average_cost == Decimal("0")
    assert position.cost_basis == Decimal("0")
    assert position.market_price is None
    assert position.price_observed_at is None
    assert position.market_value == Decimal("0")
    assert position.unrealized_pnl == Decimal("0")
    assert position.total_pnl == position.realized_pnl
    assert len(result.currencies) == 1
    assert result.currencies[0].realized_pnl == position.realized_pnl
    assert tuple(portfolio.assets) == assets_before
    assert tuple(portfolio.transactions) == transactions_before
    assert_read_only(unit_of_work)


def test_closed_and_open_positions_preserve_order_and_same_currency_aggregation() -> None:
    closed_asset = make_asset(identity=50, symbol="CLOSED")
    open_asset = make_asset(identity=51, symbol="OPEN")
    portfolio = make_portfolio(
        identity=52,
        name="Mixed",
        assets=(open_asset, closed_asset),
    )
    record_transaction(
        portfolio,
        closed_asset,
        identity=53,
        quantity="2",
        price="10",
        transaction_type=TransactionType.BUY,
    )
    record_transaction(
        portfolio,
        open_asset,
        identity=54,
        quantity="3",
        price="20",
        transaction_type=TransactionType.BUY,
    )
    record_transaction(
        portfolio,
        closed_asset,
        identity=55,
        quantity="2",
        price="15",
        transaction_type=TransactionType.SELL,
        date=LATER,
    )
    ignored_closed_price = make_price(closed_asset, identity=56, price="500")
    open_price = make_price(open_asset, identity=57, price="25")
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={
            closed_asset.id: ignored_closed_price,
            open_asset.id: open_price,
        },
    )
    service = DefaultPortfolioDashboardQueryService(factory)

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert tuple(asset.id for asset in result.portfolio.assets) == (
        open_asset.id,
        closed_asset.id,
    )
    assert tuple(transaction.id for transaction in result.portfolio.transactions) == (
        UUID(int=53),
        UUID(int=54),
        UUID(int=55),
    )
    assert tuple(position.asset_id for position in result.positions) == (
        closed_asset.id,
        open_asset.id,
    )
    assert unit_of_work.price_repository.latest_calls == [open_asset.id]
    assert result.positions[0].price_observed_at is None
    assert result.positions[1].price_observed_at is LATER
    assert len(result.currencies) == 1
    assert_dashboard_matches_domain(result, portfolio, {open_asset.id: open_price})
    assert_read_only(unit_of_work)


def test_reopened_position_requires_price_and_preserves_previous_realized_pnl() -> None:
    asset = make_asset(identity=60, symbol="REOPEN")
    portfolio = make_portfolio(identity=61, name="Reopened", assets=(asset,))
    record_transaction(
        portfolio,
        asset,
        identity=62,
        quantity="10",
        price="100",
        transaction_type=TransactionType.BUY,
    )
    record_transaction(
        portfolio,
        asset,
        identity=63,
        quantity="10",
        price="130",
        transaction_type=TransactionType.SELL,
    )
    record_transaction(
        portfolio,
        asset,
        identity=64,
        quantity="5",
        price="80",
        transaction_type=TransactionType.BUY,
        date=LATER,
    )
    latest = make_price(asset, identity=65, price="90")
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={asset.id: latest},
    )
    service = DefaultPortfolioDashboardQueryService(factory)

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    position = result.positions[0]
    assert position.quantity == Decimal("5")
    assert position.average_cost == Decimal("80")
    assert position.cost_basis == Decimal("400")
    assert position.realized_pnl == Decimal("300")
    assert position.price_observed_at is LATER
    assert only_unit_of_work(factory).price_repository.latest_calls == [asset.id]
    assert_read_only(only_unit_of_work(factory))


def test_missing_open_price_propagates_domain_error_without_mutation_or_commit() -> None:
    asset = make_asset(identity=70, symbol="MISSING")
    portfolio = make_portfolio(identity=71, name="Missing", assets=(asset,))
    record_transaction(
        portfolio,
        asset,
        identity=72,
        quantity="1",
        price="10",
        transaction_type=TransactionType.BUY,
    )
    factory = TrackingUnitOfWorkFactory(portfolios=(portfolio,))
    service = DefaultPortfolioDashboardQueryService(factory)
    assets_before = tuple(portfolio.assets)
    transactions_before = tuple(portfolio.transactions)

    with pytest.raises(MissingMarketPriceError) as raised:
        service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert raised.value.asset_id == asset.id
    assert unit_of_work.price_repository.latest_calls == [asset.id]
    assert tuple(portfolio.assets) == assets_before
    assert tuple(portfolio.transactions) == transactions_before
    assert unit_of_work.exit_exceptions == [MissingMarketPriceError]
    assert_read_only(unit_of_work)


def test_multiple_open_duplicate_symbols_use_uuid_prices_in_position_order() -> None:
    first_asset = make_asset(identity=80, symbol="DUP")
    second_asset = make_asset(identity=81, symbol="dup")
    portfolio = make_portfolio(
        identity=82,
        name="Duplicate Symbols",
        assets=(second_asset, first_asset),
    )
    record_transaction(
        portfolio,
        first_asset,
        identity=83,
        quantity="2",
        price="10",
        transaction_type=TransactionType.BUY,
    )
    record_transaction(
        portfolio,
        second_asset,
        identity=84,
        quantity="3",
        price="20",
        transaction_type=TransactionType.BUY,
    )
    first_price = make_price(first_asset, identity=85, price="15")
    second_price = make_price(second_asset, identity=86, price="30")
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={
            first_asset.id: first_price,
            second_asset.id: second_price,
        },
    )
    service = DefaultPortfolioDashboardQueryService(factory)

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert first_asset.symbol == second_asset.symbol == "DUP"
    assert unit_of_work.price_repository.latest_calls == [
        first_asset.id,
        second_asset.id,
    ]
    assert tuple(position.asset_id for position in result.positions) == (
        first_asset.id,
        second_asset.id,
    )
    assert tuple(position.symbol for position in result.positions) == ("DUP", "DUP")
    assert result.positions[0].market_price == first_price.price
    assert result.positions[1].market_price == second_price.price
    assert result.positions[0].market_value != result.positions[1].market_value
    assert_dashboard_matches_domain(
        result,
        portfolio,
        {
            first_asset.id: first_price,
            second_asset.id: second_price,
        },
    )
    assert_read_only(unit_of_work)


def test_multiple_currencies_remain_separate_in_domain_order_without_grand_total() -> None:
    usd_asset = make_asset(identity=90, symbol="USD", currency=Currency.USD)
    eur_asset = make_asset(identity=91, symbol="EUR", currency=Currency.EUR)
    portfolio = make_portfolio(
        identity=92,
        name="Currencies",
        assets=(eur_asset, usd_asset),
        base_currency=Currency.TRY,
    )
    record_transaction(
        portfolio,
        usd_asset,
        identity=93,
        quantity="2",
        price="100",
        transaction_type=TransactionType.BUY,
    )
    record_transaction(
        portfolio,
        eur_asset,
        identity=94,
        quantity="3",
        price="50",
        transaction_type=TransactionType.BUY,
    )
    usd_price = make_price(usd_asset, identity=95, price="110")
    eur_price = make_price(eur_asset, identity=96, price="60")
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={
            usd_asset.id: usd_price,
            eur_asset.id: eur_price,
        },
    )
    service = DefaultPortfolioDashboardQueryService(factory)

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    assert tuple(item.currency for item in result.currencies) == (
        Currency.USD,
        Currency.EUR,
    )
    assert result.portfolio.base_currency is Currency.TRY
    assert not hasattr(result, "total_cost_basis")
    assert not hasattr(result, "total_market_value")
    assert not hasattr(result, "total_pnl")
    assert_dashboard_matches_domain(
        result,
        portfolio,
        {
            usd_asset.id: usd_price,
            eur_asset.id: eur_price,
        },
    )
    assert_read_only(only_unit_of_work(factory))


def test_unknown_portfolio_stops_before_calculation_or_price_loading() -> None:
    missing_id = UUID(int=999)
    position_calculator = RecordingPositionCalculator()
    valuation_calculator = RecordingValuationCalculator()
    factory = TrackingUnitOfWorkFactory()
    service = DefaultPortfolioDashboardQueryService(
        factory,
        position_calculator=position_calculator,
        valuation_calculator=valuation_calculator,
    )

    with pytest.raises(PortfolioNotFoundError, match=str(missing_id)):
        service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=missing_id))

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.portfolio_repository.get_calls == [missing_id]
    assert position_calculator.calls == []
    assert valuation_calculator.calls == []
    assert unit_of_work.price_repository.latest_calls == []
    assert unit_of_work.exit_exceptions == [PortfolioNotFoundError]
    assert_read_only(unit_of_work)


@pytest.mark.parametrize(
    ("transaction_type", "expected_error"),
    (
        (TransactionType.DIVIDEND, UnsupportedTransactionTypeError),
        (TransactionType.SELL, InsufficientPositionError),
    ),
)
def test_preliminary_position_errors_propagate_before_price_or_valuation(
    transaction_type: TransactionType,
    expected_error: type[Exception],
) -> None:
    asset = make_asset(identity=100, symbol="INVALID")
    portfolio = make_portfolio(identity=101, name="Invalid", assets=(asset,))
    record_transaction(
        portfolio,
        asset,
        identity=102,
        quantity="2",
        price="10",
        transaction_type=transaction_type,
    )
    valuation_calculator = RecordingValuationCalculator()
    factory = TrackingUnitOfWorkFactory(portfolios=(portfolio,))
    service = DefaultPortfolioDashboardQueryService(
        factory,
        valuation_calculator=valuation_calculator,
    )
    assets_before = tuple(portfolio.assets)
    transactions_before = tuple(portfolio.transactions)

    with pytest.raises(expected_error):
        service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.price_repository.latest_calls == []
    assert valuation_calculator.calls == []
    assert tuple(portfolio.assets) == assets_before
    assert tuple(portfolio.transactions) == transactions_before
    assert_read_only(unit_of_work)


def test_price_repository_failure_propagates_before_valuation_without_mutation() -> None:
    asset = make_asset(identity=110, symbol="FAIL")
    portfolio = make_portfolio(identity=111, name="Repository Failure", assets=(asset,))
    record_transaction(
        portfolio,
        asset,
        identity=112,
        quantity="1",
        price="10",
        transaction_type=TransactionType.BUY,
    )
    failure = RepositoryError("latest lookup failed")
    valuation_calculator = RecordingValuationCalculator()
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_error=failure,
    )
    service = DefaultPortfolioDashboardQueryService(
        factory,
        valuation_calculator=valuation_calculator,
    )
    assets_before = tuple(portfolio.assets)
    transactions_before = tuple(portfolio.transactions)

    with pytest.raises(RepositoryError) as raised:
        service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert raised.value is failure
    assert unit_of_work.price_repository.latest_calls == [asset.id]
    assert valuation_calculator.calls == []
    assert tuple(portfolio.assets) == assets_before
    assert tuple(portfolio.transactions) == transactions_before
    assert unit_of_work.exit_exceptions == [RepositoryError]
    assert_read_only(unit_of_work)


def test_missing_repository_price_is_omitted_and_remains_valuation_owned() -> None:
    asset = make_asset(identity=120, symbol="MISSING")
    portfolio = make_portfolio(identity=121, name="Valuation Boundary", assets=(asset,))
    record_transaction(
        portfolio,
        asset,
        identity=122,
        quantity="1",
        price="10",
        transaction_type=TransactionType.BUY,
    )
    failure = MissingMarketPriceError(asset.id)
    valuation_calculator = RecordingValuationCalculator(error=failure)
    factory = TrackingUnitOfWorkFactory(portfolios=(portfolio,))
    service = DefaultPortfolioDashboardQueryService(
        factory,
        valuation_calculator=valuation_calculator,
    )

    with pytest.raises(MissingMarketPriceError) as raised:
        service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert raised.value is failure
    assert valuation_calculator.calls == [(portfolio, {})]
    assert unit_of_work.price_repository.latest_calls == [asset.id]
    assert_read_only(unit_of_work)


def test_injected_calculators_are_used_and_position_result_drives_price_queries() -> None:
    first_asset = make_asset(identity=130, symbol="OPEN")
    second_asset = make_asset(identity=131, symbol="CLOSED")
    portfolio = make_portfolio(
        identity=132,
        name="Injected",
        assets=(first_asset, second_asset),
    )
    open_position = AssetPosition(
        asset_id=first_asset.id,
        quantity=Decimal("2"),
        average_cost=Decimal("10"),
        cost_basis=Decimal("20"),
        realized_pnl=Decimal("0"),
    )
    closed_position = AssetPosition(
        asset_id=second_asset.id,
        quantity=Decimal("0"),
        average_cost=Decimal("0"),
        cost_basis=Decimal("0"),
        realized_pnl=Decimal("5"),
    )
    latest = make_price(first_asset, identity=133, price="12")
    position_calculator = RecordingPositionCalculator(
        result=(open_position, closed_position),
    )
    valuation_calculator = RecordingValuationCalculator(
        result=PortfolioValuation(positions=(), currencies=()),
    )
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={first_asset.id: latest},
    )
    service = DefaultPortfolioDashboardQueryService(
        factory,
        position_calculator=position_calculator,
        valuation_calculator=valuation_calculator,
    )

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert position_calculator.calls == [portfolio]
    assert unit_of_work.price_repository.latest_calls == [first_asset.id]
    assert valuation_calculator.calls == [(portfolio, {first_asset.id: latest.price})]
    assert result.positions == ()
    assert result.currencies == ()
    assert_read_only(unit_of_work)


def test_default_valuation_reuses_injected_position_calculator() -> None:
    portfolio = make_portfolio(identity=140, name="Composition")
    position_calculator = RecordingPositionCalculator(result=())
    factory = TrackingUnitOfWorkFactory(portfolios=(portfolio,))
    service = DefaultPortfolioDashboardQueryService(
        factory,
        position_calculator=position_calculator,
    )

    result = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    assert result.positions == ()
    assert result.currencies == ()
    assert position_calculator.calls == [portfolio, portfolio]
    assert_read_only(only_unit_of_work(factory))


def test_injected_position_failure_propagates_without_price_or_valuation() -> None:
    portfolio = make_portfolio(identity=150, name="Calculator Failure")
    failure = RuntimeError("position calculation failed")
    position_calculator = RecordingPositionCalculator(error=failure)
    valuation_calculator = RecordingValuationCalculator()
    factory = TrackingUnitOfWorkFactory(portfolios=(portfolio,))
    service = DefaultPortfolioDashboardQueryService(
        factory,
        position_calculator=position_calculator,
        valuation_calculator=valuation_calculator,
    )
    assets_before = tuple(portfolio.assets)
    transactions_before = tuple(portfolio.transactions)

    with pytest.raises(RuntimeError) as raised:
        service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert raised.value is failure
    assert position_calculator.calls == [portfolio]
    assert valuation_calculator.calls == []
    assert unit_of_work.price_repository.latest_calls == []
    assert tuple(portfolio.assets) == assets_before
    assert tuple(portfolio.transactions) == transactions_before
    assert_read_only(unit_of_work)


def test_defensive_mapping_uses_domain_error_for_missing_asset_metadata() -> None:
    portfolio = make_portfolio(identity=160, name="Missing Metadata")
    missing_asset_id = UUID(int=161)
    valuation = PortfolioValuation(
        positions=(
            ValuedAssetPosition(
                asset_id=missing_asset_id,
                currency=Currency.USD,
                quantity=Decimal("0"),
                average_cost=Decimal("0"),
                cost_basis=Decimal("0"),
                market_price=None,
                market_value=Decimal("0"),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                total_pnl=Decimal("0"),
            ),
        ),
        currencies=(),
    )
    position_calculator = RecordingPositionCalculator(result=())
    valuation_calculator = RecordingValuationCalculator(result=valuation)
    factory = TrackingUnitOfWorkFactory(portfolios=(portfolio,))
    service = DefaultPortfolioDashboardQueryService(
        factory,
        position_calculator=position_calculator,
        valuation_calculator=valuation_calculator,
    )

    with pytest.raises(PositionAssetNotFoundError) as raised:
        service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    assert raised.value.asset_id == missing_asset_id
    assert_read_only(only_unit_of_work(factory))


def test_repeated_dashboard_reads_use_fresh_units_and_return_equal_results() -> None:
    asset = make_asset(identity=170, symbol="REPEAT")
    portfolio = make_portfolio(identity=171, name="Repeat", assets=(asset,))
    record_transaction(
        portfolio,
        asset,
        identity=172,
        quantity="2",
        price="10",
        transaction_type=TransactionType.BUY,
    )
    latest = make_price(asset, identity=173, price="12")
    factory = TrackingUnitOfWorkFactory(
        portfolios=(portfolio,),
        latest_by_asset={asset.id: latest},
    )
    service = DefaultPortfolioDashboardQueryService(factory)
    assets_before = tuple(portfolio.assets)
    transactions_before = tuple(portfolio.transactions)

    first = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))
    second = service.get_dashboard(GetPortfolioDashboardQuery(portfolio_id=portfolio.id))

    assert first == second
    assert len(factory.created) == 2
    assert factory.created[0] is not factory.created[1]
    assert all(unit_of_work.commit_count == 0 for unit_of_work in factory.created)
    assert all(
        unit_of_work.price_repository.latest_calls == [asset.id] for unit_of_work in factory.created
    )
    assert tuple(portfolio.assets) == assets_before
    assert tuple(portfolio.transactions) == transactions_before
    for unit_of_work in factory.created:
        assert_read_only(unit_of_work)


def test_dashboard_service_has_no_forbidden_dependency_or_calculation_formula() -> None:
    source = SERVICES_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SERVICES_PATH))
    imported_modules = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    service_source = inspect.getsource(DefaultPortfolioDashboardQueryService)
    mapper_source = (
        inspect.getsource(services_module._to_portfolio_dashboard)
        + inspect.getsource(services_module._to_valued_asset_position_view)
        + inspect.getsource(services_module._to_currency_valuation_view)
    )
    service_tree = ast.parse(service_source)
    mapper_tree = ast.parse(mapper_source)
    forbidden_imports = {
        "sqlalchemy",
        "sqlite3",
        "alembic",
        "PySide6",
        "app.infrastructure",
        "app.ui",
    }

    assert not {
        imported
        for imported in imported_modules
        if any(
            imported == forbidden or imported.startswith(f"{forbidden}.")
            for forbidden in forbidden_imports
        )
    }
    for forbidden_behavior in (
        ".commit(",
        ".rollback(",
        ".add(",
        ".save(",
        ".delete(",
        ".list(",
        "sorted(",
        "get_by_symbol",
        "observed_at.desc",
        "id.desc",
        "Session",
        "select(",
        "text(",
        "float(",
        ".quantize(",
        "round(",
        "datetime.now",
        "DefaultMarketPriceApplicationService",
        "SQLAlchemy",
        "provider",
        "exchange_rate",
        "base_currency_total",
        "fallback",
        "._transactions",
        "._assets",
    ):
        assert forbidden_behavior not in service_source + mapper_source
    assert "get_latest_for_asset" in service_source
    assert "position.quantity == _ZERO" in service_source
    assert "position.quantity:" not in service_source
    assert not any(
        isinstance(node, ast.BinOp)
        and isinstance(
            node.op,
            (
                ast.Add,
                ast.Sub,
                ast.Mult,
                ast.Div,
                ast.FloorDiv,
                ast.Mod,
                ast.Pow,
            ),
        )
        for node in (*ast.walk(service_tree), *ast.walk(mapper_tree))
    )
    assert service_source.count("with self._unit_of_work_factory()") == 1
    assert "PortfolioValuationCalculator(" in service_source
