"""Focused tests for pure portfolio valuation calculations."""

import ast
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.exceptions import (
    DomainError,
    InsufficientPositionError,
    InvalidMarketPriceError,
    MissingMarketPriceError,
    PositionAssetNotFoundError,
    UnsupportedTransactionTypeError,
)
from app.domain.services import PortfolioValuationCalculator
from app.domain.value_objects import (
    AssetPosition,
    CurrencyValuation,
    PortfolioValuation,
    ValuedAssetPosition,
)
from app.domain.value_objects.currency import Currency

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CALCULATOR_MODULE = PROJECT_ROOT / "app/domain/services/portfolio_valuation_calculator.py"
RESULT_MODULE = PROJECT_ROOT / "app/domain/value_objects/portfolio_valuation.py"
EXCEPTION_MODULE = PROJECT_ROOT / "app/domain/exceptions/__init__.py"


class StubPositionCalculator:
    """Return isolated position results for valuation boundary tests."""

    def __init__(self, positions: tuple[AssetPosition, ...]) -> None:
        self.positions = positions
        self.calls: list[Portfolio] = []

    def calculate(self, portfolio: Portfolio) -> tuple[AssetPosition, ...]:
        self.calls.append(portfolio)
        return self.positions


def make_asset(
    *,
    identity: int,
    symbol: str,
    currency: Currency,
) -> Asset:
    return Asset(
        id=UUID(int=identity),
        symbol=symbol,
        name=f"{symbol} Asset",
        asset_type=AssetType.EQUITY,
        currency=currency,
        created_at=NOW,
    )


def make_portfolio(
    *assets: Asset,
    base_currency: Currency = Currency.TRY,
) -> Portfolio:
    portfolio = Portfolio(
        id=UUID(int=100),
        name="Valued",
        base_currency=base_currency,
        created_at=NOW,
    )
    for asset in assets:
        portfolio.add_asset(asset)
    return portfolio


def record(
    portfolio: Portfolio,
    *,
    identity: int,
    asset: Asset,
    transaction_type: TransactionType,
    quantity: str,
    price: str,
    commission: str = "0",
    tax: str = "0",
) -> Transaction:
    transaction = Transaction(
        id=UUID(int=identity),
        asset_id=asset.id,
        quantity=Decimal(quantity),
        price=Decimal(price),
        transaction_type=transaction_type,
        commission=Decimal(commission),
        tax=Decimal(tax),
        date=NOW,
    )
    portfolio.record_transaction(transaction)
    return transaction


def calculate_one(
    portfolio: Portfolio,
    market_prices: dict[UUID, Decimal],
) -> ValuedAssetPosition:
    valuation = PortfolioValuationCalculator().calculate(portfolio, market_prices)
    assert len(valuation.positions) == 1
    assert len(valuation.currencies) == 1
    return valuation.positions[0]


def assert_valued_position(
    position: ValuedAssetPosition,
    *,
    asset_id: UUID,
    currency: Currency,
    quantity: str,
    average_cost: str,
    cost_basis: str,
    market_price: str | None,
    market_value: str,
    realized_pnl: str,
    unrealized_pnl: str,
    total_pnl: str,
) -> None:
    assert position == ValuedAssetPosition(
        asset_id=asset_id,
        currency=currency,
        quantity=Decimal(quantity),
        average_cost=Decimal(average_cost),
        cost_basis=Decimal(cost_basis),
        market_price=Decimal(market_price) if market_price is not None else None,
        market_value=Decimal(market_value),
        realized_pnl=Decimal(realized_pnl),
        unrealized_pnl=Decimal(unrealized_pnl),
        total_pnl=Decimal(total_pnl),
    )


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_empty_portfolio_returns_empty_immutable_results_without_prices() -> None:
    portfolio = make_portfolio()

    result = PortfolioValuationCalculator().calculate(portfolio, {})

    assert result == PortfolioValuation(positions=(), currencies=())
    assert isinstance(result.positions, tuple)
    assert isinstance(result.currencies, tuple)


def test_single_open_position_calculates_market_and_profit_values() -> None:
    asset = make_asset(identity=1, symbol="OPEN", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
    )
    market_price = Decimal("130")

    position = calculate_one(portfolio, {asset.id: market_price})

    assert_valued_position(
        position,
        asset_id=asset.id,
        currency=Currency.TRY,
        quantity="10",
        average_cost="100",
        cost_basis="1000",
        market_price="130",
        market_value="1300",
        realized_pnl="0",
        unrealized_pnl="300",
        total_pnl="300",
    )
    assert position.market_price is market_price


def test_acquisition_charges_are_reflected_once_in_unrealized_pnl() -> None:
    asset = make_asset(identity=1, symbol="CHARGED", currency=Currency.USD)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
        commission="10",
        tax="5",
    )

    position = calculate_one(portfolio, {asset.id: Decimal("110")})

    assert_valued_position(
        position,
        asset_id=asset.id,
        currency=Currency.USD,
        quantity="10",
        average_cost="101.5",
        cost_basis="1015",
        market_price="110",
        market_value="1100",
        realized_pnl="0",
        unrealized_pnl="85",
        total_pnl="85",
    )


def test_partial_sale_combines_position_realized_and_current_unrealized_pnl() -> None:
    asset = make_asset(identity=1, symbol="PARTIAL", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="4",
        price="130",
    )

    position = calculate_one(portfolio, {asset.id: Decimal("110")})

    assert_valued_position(
        position,
        asset_id=asset.id,
        currency=Currency.TRY,
        quantity="6",
        average_cost="100",
        cost_basis="600",
        market_price="110",
        market_value="660",
        realized_pnl="120",
        unrealized_pnl="60",
        total_pnl="180",
    )


def test_sell_charges_already_in_realized_pnl_are_not_applied_again() -> None:
    asset = make_asset(identity=1, symbol="SELLFEE", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="4",
        price="130",
        commission="5",
        tax="3",
    )

    position = calculate_one(portfolio, {asset.id: Decimal("100")})

    assert position.quantity == Decimal("6")
    assert position.market_value == Decimal("600")
    assert position.realized_pnl == Decimal("112")
    assert position.unrealized_pnl == Decimal("0")
    assert position.total_pnl == Decimal("112")


def test_fully_closed_profitable_position_needs_no_price_and_remains_included() -> None:
    asset = make_asset(identity=1, symbol="CLOSEDWIN", currency=Currency.USD)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="10",
        price="130",
    )

    position = calculate_one(portfolio, {})

    assert_valued_position(
        position,
        asset_id=asset.id,
        currency=Currency.USD,
        quantity="0",
        average_cost="0",
        cost_basis="0",
        market_price=None,
        market_value="0",
        realized_pnl="300",
        unrealized_pnl="0",
        total_pnl="300",
    )


def test_fully_closed_loss_position_needs_no_price_and_preserves_loss() -> None:
    asset = make_asset(identity=1, symbol="CLOSEDLOSS", currency=Currency.EUR)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="10",
        price="80",
    )

    position = calculate_one(portfolio, {})

    assert position.market_price is None
    assert position.market_value == Decimal("0")
    assert position.realized_pnl == Decimal("-200")
    assert position.unrealized_pnl == Decimal("0")
    assert position.total_pnl == Decimal("-200")


def test_reopened_position_uses_current_cycle_and_cumulative_realized_pnl() -> None:
    asset = make_asset(identity=1, symbol="REOPEN", currency=Currency.GBP)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="10",
        price="130",
    )
    record(
        portfolio,
        identity=3,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="5",
        price="80",
    )

    position = calculate_one(portfolio, {asset.id: Decimal("100")})

    assert_valued_position(
        position,
        asset_id=asset.id,
        currency=Currency.GBP,
        quantity="5",
        average_cost="80",
        cost_basis="400",
        market_price="100",
        market_value="500",
        realized_pnl="300",
        unrealized_pnl="100",
        total_pnl="400",
    )


def test_zero_market_price_is_valid_for_an_open_position() -> None:
    asset = make_asset(identity=1, symbol="ZERO", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
    )

    position = calculate_one(portfolio, {asset.id: Decimal("0")})

    assert position.market_price == Decimal("0")
    assert position.market_value == Decimal("0")
    assert position.unrealized_pnl == Decimal("-1000")
    assert position.total_pnl == Decimal("-1000")


def test_missing_open_position_price_raises_without_mutating_inputs() -> None:
    priced = make_asset(identity=1, symbol="PRICED", currency=Currency.TRY)
    missing = make_asset(identity=2, symbol="MISSING", currency=Currency.USD)
    portfolio = make_portfolio(priced, missing)
    record(
        portfolio,
        identity=1,
        asset=priced,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    record(
        portfolio,
        identity=2,
        asset=missing,
        transaction_type=TransactionType.BUY,
        quantity="2",
        price="20",
    )
    market_prices = {priced.id: Decimal("15")}
    prices_before = market_prices.copy()
    transactions_before = tuple(portfolio.transactions)
    assets_before = tuple(portfolio.assets)

    with pytest.raises(MissingMarketPriceError) as raised:
        PortfolioValuationCalculator().calculate(portfolio, market_prices)

    assert isinstance(raised.value, DomainError)
    assert raised.value.asset_id is missing.id
    assert market_prices == prices_before
    assert tuple(portfolio.transactions) == transactions_before
    assert tuple(portfolio.assets) == assets_before


def test_closed_position_ignores_missing_and_invalid_supplied_prices() -> None:
    asset = make_asset(identity=1, symbol="IGNORE", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="1",
        price="10",
    )

    missing_result = PortfolioValuationCalculator().calculate(portfolio, {})
    invalid_result = PortfolioValuationCalculator().calculate(
        portfolio,
        {asset.id: Decimal("-99")},
    )

    assert missing_result == invalid_result
    assert missing_result.positions[0].market_price is None


def test_negative_open_position_price_raises_contextual_domain_error() -> None:
    asset = make_asset(identity=1, symbol="NEGATIVE", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    market_price = Decimal("-0.01")

    with pytest.raises(InvalidMarketPriceError) as raised:
        PortfolioValuationCalculator().calculate(
            portfolio,
            {asset.id: market_price},
        )

    assert isinstance(raised.value, DomainError)
    assert raised.value.asset_id is asset.id
    assert raised.value.market_price is market_price


def test_non_decimal_open_position_price_is_rejected_without_conversion() -> None:
    asset = make_asset(identity=1, symbol="TYPE", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    runtime_float = cast(Decimal, 13 / 2)

    with pytest.raises(TypeError, match="must be a Decimal"):
        PortfolioValuationCalculator().calculate(
            portfolio,
            {asset.id: runtime_float},
        )


def test_extra_market_prices_are_ignored_and_mapping_is_unchanged() -> None:
    asset = make_asset(identity=1, symbol="USED", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    unrelated_id = UUID(int=999)
    market_prices = {
        asset.id: Decimal("12"),
        unrelated_id: Decimal("-100"),
    }
    prices_before = market_prices.copy()

    result = PortfolioValuationCalculator().calculate(portfolio, market_prices)

    assert result.positions[0].market_price == Decimal("12")
    assert market_prices == prices_before


def test_prices_are_resolved_by_uuid_when_symbols_are_duplicated() -> None:
    first = make_asset(identity=1, symbol="DUP", currency=Currency.TRY)
    second = make_asset(identity=2, symbol="DUP", currency=Currency.TRY)
    portfolio = make_portfolio(first, second)
    record(
        portfolio,
        identity=1,
        asset=first,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    record(
        portfolio,
        identity=2,
        asset=second,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )

    result = PortfolioValuationCalculator().calculate(
        portfolio,
        {
            first.id: Decimal("11"),
            second.id: Decimal("22"),
        },
    )

    assert tuple(position.market_value for position in result.positions) == (
        Decimal("11"),
        Decimal("22"),
    )


def test_same_currency_positions_sum_exact_currency_values() -> None:
    open_asset = make_asset(identity=1, symbol="OPEN", currency=Currency.USD)
    closed_asset = make_asset(identity=2, symbol="CLOSED", currency=Currency.USD)
    portfolio = make_portfolio(open_asset, closed_asset)
    record(
        portfolio,
        identity=1,
        asset=open_asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
    )
    record(
        portfolio,
        identity=2,
        asset=closed_asset,
        transaction_type=TransactionType.BUY,
        quantity="5",
        price="50",
    )
    record(
        portfolio,
        identity=3,
        asset=closed_asset,
        transaction_type=TransactionType.SELL,
        quantity="5",
        price="60",
    )

    result = PortfolioValuationCalculator().calculate(
        portfolio,
        {open_asset.id: Decimal("130")},
    )

    assert result.currencies == (
        CurrencyValuation(
            currency=Currency.USD,
            cost_basis=Decimal("1000"),
            market_value=Decimal("1300"),
            realized_pnl=Decimal("50"),
            unrealized_pnl=Decimal("300"),
            total_pnl=Decimal("350"),
        ),
    )


def test_multi_currency_positions_have_separate_first_seen_buckets() -> None:
    euro = make_asset(identity=1, symbol="EUR", currency=Currency.EUR)
    dollar = make_asset(identity=2, symbol="USD", currency=Currency.USD)
    portfolio = make_portfolio(euro, dollar, base_currency=Currency.GBP)
    record(
        portfolio,
        identity=1,
        asset=dollar,
        transaction_type=TransactionType.BUY,
        quantity="2",
        price="50",
    )
    record(
        portfolio,
        identity=2,
        asset=euro,
        transaction_type=TransactionType.BUY,
        quantity="4",
        price="50",
    )

    result = PortfolioValuationCalculator().calculate(
        portfolio,
        {
            euro.id: Decimal("45"),
            dollar.id: Decimal("60"),
        },
    )

    assert tuple(position.currency for position in result.positions) == (
        Currency.USD,
        Currency.EUR,
    )
    assert result.currencies == (
        CurrencyValuation(
            currency=Currency.USD,
            cost_basis=Decimal("100"),
            market_value=Decimal("120"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("20"),
            total_pnl=Decimal("20"),
        ),
        CurrencyValuation(
            currency=Currency.EUR,
            cost_basis=Decimal("200"),
            market_value=Decimal("180"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("-20"),
            total_pnl=Decimal("-20"),
        ),
    )
    assert tuple(field.name for field in fields(PortfolioValuation)) == (
        "positions",
        "currencies",
    )


def test_closed_only_currency_bucket_retains_realized_pnl() -> None:
    asset = make_asset(identity=1, symbol="HISTORY", currency=Currency.GBP)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="2",
        price="100",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="2",
        price="120",
    )

    result = PortfolioValuationCalculator().calculate(portfolio, {})

    assert result.currencies == (
        CurrencyValuation(
            currency=Currency.GBP,
            cost_basis=Decimal("0"),
            market_value=Decimal("0"),
            realized_pnl=Decimal("40"),
            unrealized_pnl=Decimal("0"),
            total_pnl=Decimal("40"),
        ),
    )


def test_mixed_open_and_closed_positions_are_all_included() -> None:
    closed = make_asset(identity=1, symbol="CLOSED", currency=Currency.TRY)
    open_asset = make_asset(identity=2, symbol="OPEN", currency=Currency.TRY)
    portfolio = make_portfolio(closed, open_asset)
    record(
        portfolio,
        identity=1,
        asset=closed,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    record(
        portfolio,
        identity=2,
        asset=closed,
        transaction_type=TransactionType.SELL,
        quantity="1",
        price="15",
    )
    record(
        portfolio,
        identity=3,
        asset=open_asset,
        transaction_type=TransactionType.BUY,
        quantity="2",
        price="20",
    )

    result = PortfolioValuationCalculator().calculate(
        portfolio,
        {open_asset.id: Decimal("25")},
    )

    assert tuple(position.asset_id for position in result.positions) == (
        closed.id,
        open_asset.id,
    )
    assert result.positions[0].market_price is None
    assert result.positions[1].market_value == Decimal("50")
    assert result.currencies[0].realized_pnl == Decimal("5")
    assert result.currencies[0].unrealized_pnl == Decimal("10")
    assert result.currencies[0].total_pnl == Decimal("15")


def test_valued_position_order_matches_first_transaction_appearance() -> None:
    first_in_assets = make_asset(identity=1, symbol="ASSET1", currency=Currency.TRY)
    first_in_transactions = make_asset(
        identity=2,
        symbol="ASSET2",
        currency=Currency.TRY,
    )
    portfolio = make_portfolio(first_in_assets, first_in_transactions)
    record(
        portfolio,
        identity=1,
        asset=first_in_transactions,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    record(
        portfolio,
        identity=2,
        asset=first_in_assets,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )

    result = PortfolioValuationCalculator().calculate(
        portfolio,
        {
            first_in_assets.id: Decimal("11"),
            first_in_transactions.id: Decimal("12"),
        },
    )

    assert tuple(position.asset_id for position in result.positions) == (
        first_in_transactions.id,
        first_in_assets.id,
    )


def test_currency_order_follows_position_order_not_enum_value() -> None:
    euro = make_asset(identity=1, symbol="EURO", currency=Currency.EUR)
    dollar = make_asset(identity=2, symbol="DOLLAR", currency=Currency.USD)
    pound = make_asset(identity=3, symbol="POUND", currency=Currency.GBP)
    portfolio = make_portfolio(euro, dollar, pound)
    for identity, asset in enumerate((dollar, pound, euro), start=1):
        record(
            portfolio,
            identity=identity,
            asset=asset,
            transaction_type=TransactionType.BUY,
            quantity="1",
            price="10",
        )

    result = PortfolioValuationCalculator().calculate(
        portfolio,
        {
            euro.id: Decimal("10"),
            dollar.id: Decimal("10"),
            pound.id: Decimal("10"),
        },
    )

    assert tuple(summary.currency for summary in result.currencies) == (
        Currency.USD,
        Currency.GBP,
        Currency.EUR,
    )


def test_missing_asset_metadata_raises_without_private_aggregate_mutation() -> None:
    missing_asset_id = UUID(int=999)
    stub = StubPositionCalculator(
        (
            AssetPosition(
                asset_id=missing_asset_id,
                quantity=Decimal("1"),
                average_cost=Decimal("10"),
                cost_basis=Decimal("10"),
                realized_pnl=Decimal("0"),
            ),
        )
    )
    portfolio = make_portfolio()
    calculator = PortfolioValuationCalculator(position_calculator=stub)

    with pytest.raises(PositionAssetNotFoundError) as raised:
        calculator.calculate(portfolio, {missing_asset_id: Decimal("12")})

    assert isinstance(raised.value, DomainError)
    assert raised.value.asset_id is missing_asset_id
    assert stub.calls == [portfolio]


def test_unsupported_transaction_type_propagates_unchanged() -> None:
    asset = make_asset(identity=1, symbol="DIVIDEND", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    transaction = record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.DIVIDEND,
        quantity="1",
        price="10",
    )

    with pytest.raises(UnsupportedTransactionTypeError) as raised:
        PortfolioValuationCalculator().calculate(portfolio, {})

    assert raised.value.transaction_id is transaction.id
    assert raised.value.transaction_type is TransactionType.DIVIDEND


def test_oversell_error_propagates_unchanged() -> None:
    asset = make_asset(identity=1, symbol="OVERSELL", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="2",
        price="12",
    )

    with pytest.raises(InsufficientPositionError) as raised:
        PortfolioValuationCalculator().calculate(portfolio, {})

    assert raised.value.asset_id is asset.id
    assert raised.value.available_quantity == Decimal("1")
    assert raised.value.requested_quantity == Decimal("2")


def test_fractional_decimal_values_are_preserved_without_rounding() -> None:
    asset = make_asset(identity=1, symbol="EXACT", currency=Currency.EUR)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="0.3",
        price="0.1",
        commission="0.002",
    )
    market_price = Decimal("0.2")

    position = calculate_one(portfolio, {asset.id: market_price})

    assert position.average_cost == Decimal("0.032") / Decimal("0.3")
    assert position.cost_basis == Decimal("0.032")
    assert position.market_value == Decimal("0.06")
    assert position.unrealized_pnl == Decimal("0.028")
    assert position.total_pnl == Decimal("0.028")
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


def test_results_and_collections_are_immutable_and_inputs_are_unchanged() -> None:
    asset = make_asset(identity=1, symbol="IMMUTABLE", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    market_prices = {asset.id: Decimal("12")}
    prices_before = market_prices.copy()
    transactions_before = tuple(portfolio.transactions)
    assets_before = tuple(portfolio.assets)

    result = PortfolioValuationCalculator().calculate(portfolio, market_prices)

    with pytest.raises(FrozenInstanceError):
        setattr(result.positions[0], "market_value", Decimal("99"))
    with pytest.raises(FrozenInstanceError):
        setattr(result.currencies[0], "market_value", Decimal("99"))
    with pytest.raises(FrozenInstanceError):
        setattr(result, "positions", ())
    assert isinstance(result.positions, tuple)
    assert isinstance(result.currencies, tuple)
    assert market_prices == prices_before
    assert tuple(portfolio.transactions) == transactions_before
    assert tuple(portfolio.assets) == assets_before


def test_repeated_calculation_is_equal_and_retains_no_internal_state() -> None:
    asset = make_asset(identity=1, symbol="REPEAT", currency=Currency.TRY)
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="1",
        price="10",
    )
    prices = {asset.id: Decimal("12")}
    calculator = PortfolioValuationCalculator()

    first = calculator.calculate(portfolio, prices)
    second = calculator.calculate(portfolio, prices)

    assert first == second
    assert first is not second
    assert first.positions is not second.positions
    assert first.currencies is not second.currencies


def test_public_valuation_result_contracts_have_only_accepted_fields() -> None:
    assert tuple(field.name for field in fields(ValuedAssetPosition)) == (
        "asset_id",
        "currency",
        "quantity",
        "average_cost",
        "cost_basis",
        "market_price",
        "market_value",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
    )
    assert tuple(field.name for field in fields(CurrencyValuation)) == (
        "currency",
        "cost_basis",
        "market_value",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
    )
    assert tuple(field.name for field in fields(PortfolioValuation)) == (
        "positions",
        "currencies",
    )


def test_valuation_engine_has_no_forbidden_architecture_dependencies() -> None:
    production_files = (
        CALCULATOR_MODULE,
        RESULT_MODULE,
        EXCEPTION_MODULE,
    )
    forbidden_imports = {
        "sqlalchemy",
        "sqlite3",
        "alembic",
        "PySide6",
        "app.application",
        "app.infrastructure",
        "app.repositories",
        "app.ui",
    }
    imports = set().union(*(imported_modules(path) for path in production_files))
    source = "\n".join(path.read_text(encoding="utf-8") for path in production_files)

    assert not {
        imported
        for imported in imports
        if any(
            imported == forbidden or imported.startswith(f"{forbidden}.")
            for forbidden in forbidden_imports
        )
    }
    for forbidden_token in (
        "PriceHistory",
        "UnitOfWork",
        "Repository",
        "Session",
        "symbol",
        "float(",
        "Decimal(str(",
        ".quantize(",
        "round(",
        "exchange_rate",
        "base_currency",
        "publish(",
        "._assets",
        "._transactions",
        ".sort(",
        "sorted(",
        "transaction.quantity",
        "transaction.price",
        "transaction.commission",
        "transaction.tax",
    ):
        assert forbidden_token not in source
    assert "PortfolioPositionCalculator" in source
