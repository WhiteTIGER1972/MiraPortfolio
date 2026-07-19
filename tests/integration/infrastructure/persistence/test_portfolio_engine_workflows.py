"""Cross-layer acceptance tests for the Sprint 1.5 portfolio engine."""

from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.application.commands import (
    BuyAssetCommand,
    CreatePortfolioCommand,
    DeleteTransactionCommand,
    SellAssetCommand,
)
from app.application.exceptions import AssetNotFoundError, PortfolioNotFoundError
from app.application.queries import GetPortfolioQuery, ListPortfoliosQuery
from app.application.results import AssetPositionView
from app.application.services import DefaultPortfolioApplicationService
from app.core.exceptions import RepositoryError
from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.exceptions import (
    InsufficientPositionError,
    TransactionNotFoundError,
    UnsupportedTransactionTypeError,
)
from app.domain.services import (
    PortfolioPositionCalculator,
    PortfolioValuationCalculator,
)
from app.domain.value_objects import (
    AssetPosition,
    CurrencyValuation,
    PortfolioValuation,
    ValuedAssetPosition,
)
from app.domain.value_objects.currency import Currency
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SQLAlchemyUnitOfWork

TRADE_ONE = datetime(2026, 7, 19, 9, 15, 10, 123456, tzinfo=UTC)
TRADE_TWO = datetime(2026, 7, 19, 10, 30, 20, 234567, tzinfo=UTC)
TRADE_THREE = datetime(2026, 7, 19, 11, 45, 30, 345678, tzinfo=UTC)
TRADE_FOUR = datetime(2026, 7, 19, 13, 0, 40, 456789, tzinfo=UTC)
ASSET_CREATED_AT = datetime(2026, 7, 18, 8, 0, 5, 987654, tzinfo=UTC)


class RealUnitOfWorkFactory:
    """Create and retain distinct real Units of Work for lifecycle assertions."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.instances: list[SQLAlchemyUnitOfWork] = []

    def __call__(self) -> SQLAlchemyUnitOfWork:
        unit_of_work = SQLAlchemyUnitOfWork(self._session_factory)
        self.instances.append(unit_of_work)
        return unit_of_work


class ExpectedRollbackError(Exception):
    """Force a real Unit of Work context to exercise exception rollback."""


def make_asset(
    *,
    identity: int,
    symbol: str,
    currency: Currency,
    name: str | None = None,
) -> Asset:
    return Asset(
        id=UUID(int=identity),
        symbol=symbol,
        name=name or f"{symbol} Asset {identity}",
        asset_type=AssetType.EQUITY,
        currency=currency,
        created_at=ASSET_CREATED_AT,
    )


def make_service(
    session_factory: sessionmaker[Session],
) -> tuple[DefaultPortfolioApplicationService, RealUnitOfWorkFactory]:
    factory = RealUnitOfWorkFactory(session_factory)
    return DefaultPortfolioApplicationService(factory), factory


def seed_assets(
    session_factory: sessionmaker[Session],
    *assets: Asset,
) -> None:
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        for asset in assets:
            unit_of_work.assets.add(asset)
        unit_of_work.commit()


def load_portfolio(
    session_factory: sessionmaker[Session],
    portfolio_id: UUID,
) -> Portfolio:
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        portfolio = unit_of_work.portfolios.get(portfolio_id)
        assert portfolio is not None
        return portfolio


def create_portfolio(
    service: DefaultPortfolioApplicationService,
    *,
    name: str,
) -> UUID:
    return service.create_portfolio(CreatePortfolioCommand(portfolio_name=name)).id


def buy(
    service: DefaultPortfolioApplicationService,
    *,
    portfolio_id: UUID,
    asset_id: UUID,
    quantity: str,
    unit_price: str,
    trade_datetime: datetime,
) -> UUID:
    return service.buy_asset(
        BuyAssetCommand(
            portfolio_id=portfolio_id,
            asset_id=asset_id,
            quantity=Decimal(quantity),
            unit_price=Decimal(unit_price),
            trade_datetime=trade_datetime,
        )
    ).id


def sell(
    service: DefaultPortfolioApplicationService,
    *,
    portfolio_id: UUID,
    asset_id: UUID,
    quantity: str,
    unit_price: str,
    trade_datetime: datetime,
) -> UUID:
    return service.sell_asset(
        SellAssetCommand(
            portfolio_id=portfolio_id,
            asset_id=asset_id,
            quantity=Decimal(quantity),
            unit_price=Decimal(unit_price),
            trade_datetime=trade_datetime,
        )
    ).id


def test_create_buy_reload_calculate_value_and_query_preserves_exact_values(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=1, symbol="E2E", currency=Currency.USD)
    seed_assets(session_factory, asset)
    service, factory = make_service(session_factory)

    created = service.create_portfolio(CreatePortfolioCommand(portfolio_name="End to End"))
    transaction_id = buy(
        service,
        portfolio_id=created.id,
        asset_id=asset.id,
        quantity="10.25",
        unit_price="100.40",
        trade_datetime=TRADE_ONE,
    )
    reloaded = load_portfolio(session_factory, created.id)

    assert reloaded.id == created.id
    assert [held.id for held in reloaded.assets] == [asset.id]
    assert len(reloaded.transactions) == 1
    transaction = reloaded.transactions[0]
    assert transaction.id == transaction_id
    assert transaction.asset_id == asset.id
    assert transaction.transaction_type is TransactionType.BUY
    assert transaction.quantity == Decimal("10.25")
    assert transaction.price == Decimal("100.40")
    assert transaction.commission == Decimal("0")
    assert transaction.tax == Decimal("0")
    assert transaction.date == TRADE_ONE
    assert transaction.date.tzinfo is UTC

    positions = PortfolioPositionCalculator().calculate(reloaded)
    assert positions == (
        AssetPosition(
            asset_id=asset.id,
            quantity=Decimal("10.25"),
            average_cost=Decimal("100.40"),
            cost_basis=Decimal("1029.1000"),
            realized_pnl=Decimal("0"),
        ),
    )
    valuation = PortfolioValuationCalculator().calculate(
        reloaded,
        {asset.id: Decimal("130.20")},
    )
    assert valuation == PortfolioValuation(
        positions=(
            ValuedAssetPosition(
                asset_id=asset.id,
                currency=Currency.USD,
                quantity=Decimal("10.25"),
                average_cost=Decimal("100.40"),
                cost_basis=Decimal("1029.1000"),
                market_price=Decimal("130.20"),
                market_value=Decimal("1334.5500"),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("305.4500"),
                total_pnl=Decimal("305.4500"),
            ),
        ),
        currencies=(
            CurrencyValuation(
                currency=Currency.USD,
                cost_basis=Decimal("1029.1000"),
                market_value=Decimal("1334.5500"),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("305.4500"),
                total_pnl=Decimal("305.4500"),
            ),
        ),
    )

    details = service.get_portfolio(GetPortfolioQuery(portfolio_id=created.id))
    summaries = service.list_portfolios(ListPortfoliosQuery())
    assert details.id == reloaded.id
    assert details.created_at == created.created_at
    assert [view.id for view in details.assets] == [asset.id]
    assert [view.id for view in details.transactions] == [transaction_id]
    assert [summary.id for summary in summaries] == [created.id]
    assert len(factory.instances) == 4
    assert len({id(unit_of_work) for unit_of_work in factory.instances}) == 4


def test_partial_sell_persists_order_and_combines_realized_and_unrealized_pnl(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=2, symbol="PARTIAL", currency=Currency.TRY)
    seed_assets(session_factory, asset)
    service, _ = make_service(session_factory)
    portfolio_id = create_portfolio(service, name="Partial Sale")

    buy_id = buy(
        service,
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        quantity="10",
        unit_price="100",
        trade_datetime=TRADE_ONE,
    )
    sell_id = sell(
        service,
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        quantity="4",
        unit_price="130",
        trade_datetime=TRADE_TWO,
    )
    reloaded = load_portfolio(session_factory, portfolio_id)

    assert [transaction.id for transaction in reloaded.transactions] == [buy_id, sell_id]
    assert [transaction.transaction_type for transaction in reloaded.transactions] == [
        TransactionType.BUY,
        TransactionType.SELL,
    ]
    position = PortfolioPositionCalculator().calculate(reloaded)[0]
    assert position == AssetPosition(
        asset_id=asset.id,
        quantity=Decimal("6"),
        average_cost=Decimal("100"),
        cost_basis=Decimal("600"),
        realized_pnl=Decimal("120"),
    )
    valued = (
        PortfolioValuationCalculator()
        .calculate(
            reloaded,
            {asset.id: Decimal("140")},
        )
        .positions[0]
    )
    assert valued.market_value == Decimal("840")
    assert valued.realized_pnl == Decimal("120")
    assert valued.unrealized_pnl == Decimal("240")
    assert valued.total_pnl == Decimal("360")


def test_full_close_remains_valued_without_a_price(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=3, symbol="CLOSED", currency=Currency.EUR)
    seed_assets(session_factory, asset)
    service, _ = make_service(session_factory)
    portfolio_id = create_portfolio(service, name="Closed Position")

    buy(
        service,
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        quantity="8",
        unit_price="50",
        trade_datetime=TRADE_ONE,
    )
    sell(
        service,
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        quantity="8",
        unit_price="70",
        trade_datetime=TRADE_TWO,
    )
    reloaded = load_portfolio(session_factory, portfolio_id)
    position = PortfolioPositionCalculator().calculate(reloaded)[0]

    assert position.quantity == Decimal("0")
    assert position.average_cost == Decimal("0")
    assert position.cost_basis == Decimal("0")
    assert position.realized_pnl == Decimal("160")
    valuation = PortfolioValuationCalculator().calculate(reloaded, {})
    valued = valuation.positions[0]
    assert valued.market_price is None
    assert valued.market_value == Decimal("0")
    assert valued.unrealized_pnl == Decimal("0")
    assert valued.total_pnl == Decimal("160")
    assert valuation.currencies == (
        CurrencyValuation(
            currency=Currency.EUR,
            cost_basis=Decimal("0"),
            market_value=Decimal("0"),
            realized_pnl=Decimal("160"),
            unrealized_pnl=Decimal("0"),
            total_pnl=Decimal("160"),
        ),
    )


def test_close_and_reopen_uses_new_cost_cycle_and_preserves_realized_pnl(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=4, symbol="REOPEN", currency=Currency.GBP)
    seed_assets(session_factory, asset)
    service, _ = make_service(session_factory)
    portfolio_id = create_portfolio(service, name="Reopened Position")

    transaction_ids = [
        buy(
            service,
            portfolio_id=portfolio_id,
            asset_id=asset.id,
            quantity="10",
            unit_price="100",
            trade_datetime=TRADE_ONE,
        ),
        sell(
            service,
            portfolio_id=portfolio_id,
            asset_id=asset.id,
            quantity="10",
            unit_price="130",
            trade_datetime=TRADE_TWO,
        ),
        buy(
            service,
            portfolio_id=portfolio_id,
            asset_id=asset.id,
            quantity="5",
            unit_price="80",
            trade_datetime=TRADE_THREE,
        ),
    ]
    reloaded = load_portfolio(session_factory, portfolio_id)

    assert [transaction.id for transaction in reloaded.transactions] == transaction_ids
    position = PortfolioPositionCalculator().calculate(reloaded)[0]
    assert position == AssetPosition(
        asset_id=asset.id,
        quantity=Decimal("5"),
        average_cost=Decimal("80"),
        cost_basis=Decimal("400"),
        realized_pnl=Decimal("300"),
    )
    valued = (
        PortfolioValuationCalculator()
        .calculate(
            reloaded,
            {asset.id: Decimal("90")},
        )
        .positions[0]
    )
    assert valued.market_value == Decimal("450")
    assert valued.unrealized_pnl == Decimal("50")
    assert valued.total_pnl == Decimal("350")


def test_delete_persisted_transaction_recalculates_without_duplicates(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=5, symbol="DELETE", currency=Currency.TRY)
    seed_assets(session_factory, asset)
    service, _ = make_service(session_factory)
    portfolio_id = create_portfolio(service, name="Delete Transaction")

    first_buy_id = buy(
        service,
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        quantity="10",
        unit_price="100",
        trade_datetime=TRADE_ONE,
    )
    removed_buy_id = buy(
        service,
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        quantity="5",
        unit_price="120",
        trade_datetime=TRADE_TWO,
    )
    sell_id = sell(
        service,
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        quantity="3",
        unit_price="150",
        trade_datetime=TRADE_THREE,
    )

    result = service.delete_transaction(
        DeleteTransactionCommand(
            portfolio_id=portfolio_id,
            transaction_id=removed_buy_id,
        )
    )
    reloaded = load_portfolio(session_factory, portfolio_id)

    assert [view.id for view in result.transactions] == [first_buy_id, sell_id]
    assert [transaction.id for transaction in reloaded.transactions] == [
        first_buy_id,
        sell_id,
    ]
    assert [held.id for held in reloaded.assets] == [asset.id]
    position = PortfolioPositionCalculator().calculate(reloaded)[0]
    assert position == AssetPosition(
        asset_id=asset.id,
        quantity=Decimal("7"),
        average_cost=Decimal("100"),
        cost_basis=Decimal("700"),
        realized_pnl=Decimal("150"),
    )
    valued = (
        PortfolioValuationCalculator()
        .calculate(
            reloaded,
            {asset.id: Decimal("110")},
        )
        .positions[0]
    )
    assert valued.market_value == Decimal("770")
    assert valued.unrealized_pnl == Decimal("70")
    assert valued.total_pnl == Decimal("220")
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        assert unit_of_work.assets.exists(asset.id)
        persisted = unit_of_work.portfolios.get(portfolio_id)
        assert persisted is not None
        assert len(persisted.assets) == 1
        assert len(persisted.transactions) == 2


def test_application_absence_failures_leave_persisted_state_unchanged(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=6, symbol="FAILURE", currency=Currency.USD)
    seed_assets(session_factory, asset)
    service, _ = make_service(session_factory)
    portfolio_id = create_portfolio(service, name="Failure Atomicity")
    transaction_id = buy(
        service,
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        quantity="3",
        unit_price="25",
        trade_datetime=TRADE_ONE,
    )
    before = service.get_portfolio(GetPortfolioQuery(portfolio_id=portfolio_id))
    missing_portfolio_id = UUID(int=6001)
    missing_asset_id = UUID(int=6002)
    missing_transaction_id = UUID(int=6003)

    with pytest.raises(PortfolioNotFoundError):
        service.get_portfolio(GetPortfolioQuery(portfolio_id=missing_portfolio_id))
    with pytest.raises(PortfolioNotFoundError):
        service.buy_asset(
            BuyAssetCommand(
                portfolio_id=missing_portfolio_id,
                asset_id=asset.id,
                quantity=Decimal("1"),
                unit_price=Decimal("1"),
                trade_datetime=TRADE_TWO,
            )
        )
    with pytest.raises(AssetNotFoundError):
        service.sell_asset(
            SellAssetCommand(
                portfolio_id=portfolio_id,
                asset_id=missing_asset_id,
                quantity=Decimal("1"),
                unit_price=Decimal("30"),
                trade_datetime=TRADE_TWO,
            )
        )
    with pytest.raises(TransactionNotFoundError) as missing_transaction:
        service.delete_transaction(
            DeleteTransactionCommand(
                portfolio_id=portfolio_id,
                transaction_id=missing_transaction_id,
            )
        )

    assert missing_transaction.value.transaction_id == missing_transaction_id
    after = service.get_portfolio(GetPortfolioQuery(portfolio_id=portfolio_id))
    assert after == before
    assert [view.id for view in after.transactions] == [transaction_id]
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        assert unit_of_work.portfolios.count() == 1
        assert unit_of_work.assets.count() == 1


def test_duplicate_symbols_interleaved_assets_and_currencies_use_uuid_identity(
    session_factory: sessionmaker[Session],
) -> None:
    usd_asset = make_asset(
        identity=7,
        symbol="duplicate",
        currency=Currency.USD,
        name="USD Duplicate",
    )
    eur_asset = make_asset(
        identity=8,
        symbol="DUPLICATE",
        currency=Currency.EUR,
        name="EUR Duplicate",
    )
    seed_assets(session_factory, usd_asset, eur_asset)
    service, _ = make_service(session_factory)
    portfolio_id = create_portfolio(service, name="Duplicate Symbols")

    usd_buy_id = buy(
        service,
        portfolio_id=portfolio_id,
        asset_id=usd_asset.id,
        quantity="10",
        unit_price="100",
        trade_datetime=TRADE_ONE,
    )
    eur_buy_id = buy(
        service,
        portfolio_id=portfolio_id,
        asset_id=eur_asset.id,
        quantity="5",
        unit_price="20",
        trade_datetime=TRADE_TWO,
    )
    usd_sell_id = sell(
        service,
        portfolio_id=portfolio_id,
        asset_id=usd_asset.id,
        quantity="10",
        unit_price="130",
        trade_datetime=TRADE_THREE,
    )
    eur_second_buy_id = buy(
        service,
        portfolio_id=portfolio_id,
        asset_id=eur_asset.id,
        quantity="5",
        unit_price="30",
        trade_datetime=TRADE_FOUR,
    )
    reloaded = load_portfolio(session_factory, portfolio_id)

    assert [asset.symbol for asset in reloaded.assets] == ["DUPLICATE", "DUPLICATE"]
    assert [asset.id for asset in reloaded.assets] == [usd_asset.id, eur_asset.id]
    assert [transaction.id for transaction in reloaded.transactions] == [
        usd_buy_id,
        eur_buy_id,
        usd_sell_id,
        eur_second_buy_id,
    ]
    positions = PortfolioPositionCalculator().calculate(reloaded)
    assert positions == (
        AssetPosition(
            asset_id=usd_asset.id,
            quantity=Decimal("0"),
            average_cost=Decimal("0"),
            cost_basis=Decimal("0"),
            realized_pnl=Decimal("300"),
        ),
        AssetPosition(
            asset_id=eur_asset.id,
            quantity=Decimal("10"),
            average_cost=Decimal("25"),
            cost_basis=Decimal("250"),
            realized_pnl=Decimal("0"),
        ),
    )
    prices = {
        usd_asset.id: Decimal("-1"),
        eur_asset.id: Decimal("40"),
        UUID(int=7999): Decimal("999"),
    }
    original_prices = prices.copy()
    valuation = PortfolioValuationCalculator().calculate(reloaded, prices)

    assert prices == original_prices
    assert [position.asset_id for position in valuation.positions] == [
        usd_asset.id,
        eur_asset.id,
    ]
    assert valuation.positions[0].market_price is None
    assert valuation.positions[1].market_price == Decimal("40")
    assert valuation.currencies == (
        CurrencyValuation(
            currency=Currency.USD,
            cost_basis=Decimal("0"),
            market_value=Decimal("0"),
            realized_pnl=Decimal("300"),
            unrealized_pnl=Decimal("0"),
            total_pnl=Decimal("300"),
        ),
        CurrencyValuation(
            currency=Currency.EUR,
            cost_basis=Decimal("250"),
            market_value=Decimal("400"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("150"),
            total_pnl=Decimal("150"),
        ),
    )
    assert not hasattr(valuation, "total_market_value")
    assert not hasattr(valuation, "total_pnl")


def test_fee_aware_zero_price_round_trip_uses_explicit_accounting_formulas(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=9, symbol="FEES", currency=Currency.GBP)
    seed_assets(session_factory, asset)
    service, _ = make_service(session_factory)
    portfolio_id = create_portfolio(service, name="Fee-Aware Zero Price")
    buy_transaction = Transaction(
        id=UUID(int=9001),
        asset_id=asset.id,
        quantity=Decimal("10"),
        price=Decimal("0"),
        transaction_type=TransactionType.BUY,
        commission=Decimal("10"),
        tax=Decimal("5"),
        date=TRADE_ONE,
    )
    sell_transaction = Transaction(
        id=UUID(int=9002),
        asset_id=asset.id,
        quantity=Decimal("4"),
        price=Decimal("0"),
        transaction_type=TransactionType.SELL,
        commission=Decimal("2"),
        tax=Decimal("1"),
        date=TRADE_TWO,
    )

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        portfolio = unit_of_work.portfolios.get(portfolio_id)
        persisted_asset = unit_of_work.assets.get(asset.id)
        assert portfolio is not None
        assert persisted_asset is not None
        portfolio.add_asset(persisted_asset)
        portfolio.record_transaction(buy_transaction)
        portfolio.record_transaction(sell_transaction)
        unit_of_work.portfolios.save(portfolio)
        unit_of_work.commit()

    reloaded = load_portfolio(session_factory, portfolio_id)
    assert reloaded.transactions == [buy_transaction, sell_transaction]
    assert reloaded.transactions[0].price == Decimal("0")
    assert reloaded.transactions[0].commission == Decimal("10")
    assert reloaded.transactions[0].tax == Decimal("5")
    assert reloaded.transactions[1].commission == Decimal("2")
    assert reloaded.transactions[1].tax == Decimal("1")
    assert reloaded.transactions[1].calculate_total() == Decimal("3")
    position = PortfolioPositionCalculator().calculate(reloaded)[0]
    assert position == AssetPosition(
        asset_id=asset.id,
        quantity=Decimal("6"),
        average_cost=Decimal("1.5"),
        cost_basis=Decimal("9.0"),
        realized_pnl=Decimal("-9.0"),
    )
    valuation = PortfolioValuationCalculator().calculate(
        reloaded,
        {asset.id: Decimal("0")},
    )
    valued = valuation.positions[0]
    assert valued.market_price == Decimal("0")
    assert valued.market_value == Decimal("0")
    assert valued.realized_pnl == Decimal("-9.0")
    assert valued.unrealized_pnl == Decimal("-9.0")
    assert valued.total_pnl == Decimal("-18.0")
    assert all(
        isinstance(value, Decimal)
        for value in (
            position.quantity,
            position.average_cost,
            position.cost_basis,
            position.realized_pnl,
            valued.market_price,
            valued.market_value,
            valued.unrealized_pnl,
            valued.total_pnl,
        )
    )


def test_real_unit_of_work_rollback_repeated_save_and_calculation_are_stable(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=10, symbol="STABLE", currency=Currency.TRY)
    seed_assets(session_factory, asset)
    service, _ = make_service(session_factory)
    portfolio_id = create_portfolio(service, name="Rollback and Repeatability")
    original_transaction_id = buy(
        service,
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        quantity="5",
        unit_price="20",
        trade_datetime=TRADE_ONE,
    )
    rolled_back_transaction = Transaction(
        id=UUID(int=10001),
        asset_id=asset.id,
        quantity=Decimal("2"),
        price=Decimal("50"),
        transaction_type=TransactionType.BUY,
        date=TRADE_TWO,
    )

    with pytest.raises(ExpectedRollbackError):
        with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
            portfolio = unit_of_work.portfolios.get(portfolio_id)
            assert portfolio is not None
            portfolio.record_transaction(rolled_back_transaction)
            unit_of_work.portfolios.save(portfolio)
            raise ExpectedRollbackError

    after_rollback = load_portfolio(session_factory, portfolio_id)
    assert [transaction.id for transaction in after_rollback.transactions] == [
        original_transaction_id
    ]
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unchanged = unit_of_work.portfolios.get(portfolio_id)
        assert unchanged is not None
        assert unit_of_work.portfolios.save(unchanged) is unchanged
        assert unit_of_work.portfolios.save(unchanged) is unchanged
        unit_of_work.commit()

    missing = Portfolio(id=UUID(int=10002), name="Must Not Upsert")
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        with pytest.raises(RepositoryError, match="does not exist"):
            unit_of_work.portfolios.save(missing)

    reloaded = load_portfolio(session_factory, portfolio_id)
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        assert unit_of_work.portfolios.count() == 1
        assert unit_of_work.assets.count() == 1
    assert [asset.id for asset in reloaded.assets] == [asset.id]
    assert [transaction.id for transaction in reloaded.transactions] == [original_transaction_id]
    position_calculator = PortfolioPositionCalculator()
    valuation_calculator = PortfolioValuationCalculator()
    first_positions = position_calculator.calculate(reloaded)
    second_positions = position_calculator.calculate(reloaded)
    first_valuation = valuation_calculator.calculate(
        reloaded,
        {asset.id: Decimal("25")},
    )
    second_valuation = valuation_calculator.calculate(
        reloaded,
        {asset.id: Decimal("25")},
    )
    assert first_positions == second_positions
    assert first_valuation == second_valuation


def test_reloaded_unsupported_and_oversell_histories_preserve_domain_errors(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=11, symbol="INVALID", currency=Currency.USD)
    seed_assets(session_factory, asset)
    service, _ = make_service(session_factory)
    unsupported_portfolio_id = create_portfolio(service, name="Unsupported History")
    buy(
        service,
        portfolio_id=unsupported_portfolio_id,
        asset_id=asset.id,
        quantity="1",
        unit_price="10",
        trade_datetime=TRADE_ONE,
    )
    unsupported_transaction = Transaction(
        id=UUID(int=11001),
        asset_id=asset.id,
        quantity=Decimal("1"),
        price=Decimal("2"),
        transaction_type=TransactionType.DIVIDEND,
        date=TRADE_TWO,
    )
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        portfolio = unit_of_work.portfolios.get(unsupported_portfolio_id)
        assert portfolio is not None
        portfolio.record_transaction(unsupported_transaction)
        unit_of_work.portfolios.save(portfolio)
        unit_of_work.commit()

    unsupported = load_portfolio(session_factory, unsupported_portfolio_id)
    original_transactions = tuple(unsupported.transactions)
    original_assets = tuple(unsupported.assets)
    with pytest.raises(UnsupportedTransactionTypeError) as position_error:
        PortfolioPositionCalculator().calculate(unsupported)
    with pytest.raises(UnsupportedTransactionTypeError) as valuation_error:
        PortfolioValuationCalculator().calculate(
            unsupported,
            {asset.id: Decimal("12")},
        )
    assert position_error.value.transaction_id == unsupported_transaction.id
    assert position_error.value.transaction_type is TransactionType.DIVIDEND
    assert valuation_error.value.transaction_id == unsupported_transaction.id
    assert tuple(unsupported.transactions) == original_transactions
    assert tuple(unsupported.assets) == original_assets

    oversell_portfolio_id = create_portfolio(service, name="Oversell History")
    buy(
        service,
        portfolio_id=oversell_portfolio_id,
        asset_id=asset.id,
        quantity="2",
        unit_price="10",
        trade_datetime=TRADE_ONE,
    )
    sell(
        service,
        portfolio_id=oversell_portfolio_id,
        asset_id=asset.id,
        quantity="3",
        unit_price="11",
        trade_datetime=TRADE_TWO,
    )
    oversell = load_portfolio(session_factory, oversell_portfolio_id)
    with pytest.raises(InsufficientPositionError) as oversell_error:
        PortfolioPositionCalculator().calculate(oversell)
    assert oversell_error.value.asset_id == asset.id
    assert oversell_error.value.available_quantity == Decimal("2")
    assert oversell_error.value.requested_quantity == Decimal("3")
    assert len(load_portfolio(session_factory, oversell_portfolio_id).transactions) == 2


def test_empty_portfolios_and_read_queries_do_not_mutate_persistence(
    session_factory: sessionmaker[Session],
) -> None:
    service, factory = make_service(session_factory)
    first = service.create_portfolio(CreatePortfolioCommand(portfolio_name="First Empty"))
    second = service.create_portfolio(CreatePortfolioCommand(portfolio_name="Second Empty"))

    reloaded = load_portfolio(session_factory, first.id)
    assert PortfolioPositionCalculator().calculate(reloaded) == ()
    assert PortfolioValuationCalculator().calculate(reloaded, {}) == PortfolioValuation(
        positions=(),
        currencies=(),
    )
    before_instance_count = len(factory.instances)
    details = service.get_portfolio(GetPortfolioQuery(portfolio_id=first.id))
    summaries = service.list_portfolios(ListPortfoliosQuery())

    assert details == first
    assert [summary.id for summary in summaries] == sorted([first.id, second.id])
    assert len(factory.instances) == before_instance_count + 2
    assert len({id(unit_of_work) for unit_of_work in factory.instances}) == len(factory.instances)
    assert {field.name for field in fields(AssetPositionView)} == {
        "id",
        "symbol",
        "name",
        "asset_type",
        "currency",
        "is_active",
        "created_at",
    }
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        assert unit_of_work.portfolios.count() == 2
        assert unit_of_work.assets.count() == 0
