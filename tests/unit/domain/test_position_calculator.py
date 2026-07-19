"""Focused tests for moving-weighted-average position calculations."""

import ast
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.exceptions import (
    DomainError,
    InsufficientPositionError,
    UnsupportedTransactionTypeError,
)
from app.domain.services import PortfolioPositionCalculator
from app.domain.value_objects import AssetPosition
from app.domain.value_objects.currency import Currency

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CALCULATOR_MODULE = PROJECT_ROOT / "app/domain/services/position_calculator.py"
POSITION_MODULE = PROJECT_ROOT / "app/domain/value_objects/asset_position.py"
EXCEPTION_MODULE = PROJECT_ROOT / "app/domain/exceptions/__init__.py"


def make_asset(*, identity: int, symbol: str) -> Asset:
    return Asset(
        id=UUID(int=identity),
        symbol=symbol,
        name=f"{symbol} Asset",
        asset_type=AssetType.EQUITY,
        currency=Currency.TRY,
        created_at=NOW,
    )


def make_portfolio(*assets: Asset) -> Portfolio:
    portfolio = Portfolio(
        id=UUID(int=100),
        name="Calculated",
        assets=[],
        transactions=[],
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


def calculate_one(portfolio: Portfolio) -> AssetPosition:
    positions = PortfolioPositionCalculator().calculate(portfolio)
    assert len(positions) == 1
    return positions[0]


def assert_position(
    position: AssetPosition,
    *,
    asset_id: UUID,
    quantity: str,
    average_cost: str,
    cost_basis: str,
    realized_pnl: str,
) -> None:
    assert position == AssetPosition(
        asset_id=asset_id,
        quantity=Decimal(quantity),
        average_cost=Decimal(average_cost),
        cost_basis=Decimal(cost_basis),
        realized_pnl=Decimal(realized_pnl),
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


def test_empty_portfolio_returns_empty_immutable_tuple() -> None:
    portfolio = make_portfolio()

    result = PortfolioPositionCalculator().calculate(portfolio)

    assert result == ()
    assert isinstance(result, tuple)


def test_single_buy_calculates_quantity_average_basis_and_zero_realized_pnl() -> None:
    asset = make_asset(identity=1, symbol="ONE")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="8",
        price="125",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="8",
        average_cost="125",
        cost_basis="1000",
        realized_pnl="0",
    )


def test_multiple_buys_use_moving_weighted_average_cost() -> None:
    asset = make_asset(identity=1, symbol="BUYS")
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
        transaction_type=TransactionType.BUY,
        quantity="5",
        price="130",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="15",
        average_cost="110",
        cost_basis="1650",
        realized_pnl="0",
    )


def test_partial_sell_preserves_average_and_calculates_realized_profit() -> None:
    asset = make_asset(identity=1, symbol="PART")
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

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="6",
        average_cost="100",
        cost_basis="600",
        realized_pnl="120",
    )


def test_multiple_buys_then_sell_use_current_weighted_average() -> None:
    asset = make_asset(identity=1, symbol="MIXED")
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
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="120",
    )
    record(
        portfolio,
        identity=3,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="5",
        price="150",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="15",
        average_cost="110",
        cost_basis="1650",
        realized_pnl="200",
    )


def test_sell_at_loss_keeps_remaining_average_cost() -> None:
    asset = make_asset(identity=1, symbol="LOSS")
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
        price="80",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="6",
        average_cost="100",
        cost_basis="600",
        realized_pnl="-80",
    )


def test_full_close_resets_position_values_and_preserves_realized_pnl() -> None:
    asset = make_asset(identity=1, symbol="CLOSE")
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

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="0",
        average_cost="0",
        cost_basis="0",
        realized_pnl="300",
    )
    assert position.quantity.as_tuple() == Decimal("0").as_tuple()
    assert position.average_cost.as_tuple() == Decimal("0").as_tuple()
    assert position.cost_basis.as_tuple() == Decimal("0").as_tuple()


def test_closed_position_reopens_with_new_cost_cycle_and_cumulative_realized_pnl() -> None:
    asset = make_asset(identity=1, symbol="REOPEN")
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

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="5",
        average_cost="80",
        cost_basis="400",
        realized_pnl="300",
    )


def test_multiple_sales_accumulate_realized_profit_and_loss() -> None:
    asset = make_asset(identity=1, symbol="SALES")
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
        quantity="2",
        price="130",
    )
    record(
        portfolio,
        identity=3,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="3",
        price="90",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="5",
        average_cost="100",
        cost_basis="500",
        realized_pnl="30",
    )


def test_interleaved_assets_are_independent_and_follow_first_transaction_order() -> None:
    unused = make_asset(identity=1, symbol="UNUSED")
    second = make_asset(identity=2, symbol="SECOND")
    first = make_asset(identity=3, symbol="FIRST")
    portfolio = make_portfolio(unused, first, second)
    record(
        portfolio,
        identity=1,
        asset=second,
        transaction_type=TransactionType.BUY,
        quantity="2",
        price="50",
    )
    record(
        portfolio,
        identity=2,
        asset=first,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
    )
    record(
        portfolio,
        identity=3,
        asset=second,
        transaction_type=TransactionType.SELL,
        quantity="2",
        price="60",
    )
    record(
        portfolio,
        identity=4,
        asset=first,
        transaction_type=TransactionType.SELL,
        quantity="4",
        price="125",
    )

    positions = PortfolioPositionCalculator().calculate(portfolio)

    assert tuple(position.asset_id for position in positions) == (second.id, first.id)
    assert_position(
        positions[0],
        asset_id=second.id,
        quantity="0",
        average_cost="0",
        cost_basis="0",
        realized_pnl="20",
    )
    assert_position(
        positions[1],
        asset_id=first.id,
        quantity="6",
        average_cost="100",
        cost_basis="600",
        realized_pnl="100",
    )
    assert unused.id not in {position.asset_id for position in positions}


def test_fully_closed_zero_pnl_position_remains_in_results() -> None:
    asset = make_asset(identity=1, symbol="ROUNDTRIP")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="2",
        price="50",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="2",
        price="50",
    )

    positions = PortfolioPositionCalculator().calculate(portfolio)

    assert len(positions) == 1
    assert_position(
        positions[0],
        asset_id=asset.id,
        quantity="0",
        average_cost="0",
        cost_basis="0",
        realized_pnl="0",
    )


def test_oversell_raises_contextual_domain_error_without_input_mutation() -> None:
    asset = make_asset(identity=1, symbol="OVER")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="3.5",
        price="100",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="4",
        price="120",
        commission="7",
        tax="3",
    )
    transactions_before = tuple(portfolio.transactions)
    assets_before = tuple(portfolio.assets)

    with pytest.raises(InsufficientPositionError) as raised:
        PortfolioPositionCalculator().calculate(portfolio)

    assert isinstance(raised.value, DomainError)
    assert raised.value.asset_id is asset.id
    assert raised.value.available_quantity == Decimal("3.5")
    assert raised.value.requested_quantity == Decimal("4")
    assert tuple(portfolio.transactions) == transactions_before
    assert tuple(portfolio.assets) == assets_before


def test_sell_before_buy_raises_insufficient_position_error() -> None:
    asset = make_asset(identity=1, symbol="EARLY")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="1",
        price="100",
    )

    with pytest.raises(InsufficientPositionError) as raised:
        PortfolioPositionCalculator().calculate(portfolio)

    assert raised.value.available_quantity == Decimal("0")
    assert raised.value.requested_quantity == Decimal("1")


def test_non_integer_decimal_values_retain_precision_without_quantization() -> None:
    asset = make_asset(identity=1, symbol="EXACT")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="0.1",
        price="0.2",
        commission="0.003",
        tax="0.002",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="0.2",
        price="0.3",
        commission="0.004",
        tax="0.001",
    )
    record(
        portfolio,
        identity=3,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="0.1",
        price="0.5",
        commission="0.002",
        tax="0.001",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="0.2",
        average_cost="0.3",
        cost_basis="0.06",
        realized_pnl="0.017",
    )
    assert all(
        isinstance(value, Decimal)
        for value in (
            position.quantity,
            position.average_cost,
            position.cost_basis,
            position.realized_pnl,
        )
    )


def test_calculation_uses_aggregate_order_instead_of_transaction_datetime() -> None:
    asset = make_asset(identity=1, symbol="ORDER")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
        date=NOW + timedelta(days=2),
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="10",
        price="130",
        date=NOW,
    )
    record(
        portfolio,
        identity=3,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="5",
        price="80",
        date=NOW + timedelta(days=1),
    )
    transaction_order = tuple(transaction.id for transaction in portfolio.transactions)

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="5",
        average_cost="80",
        cost_basis="400",
        realized_pnl="300",
    )
    assert tuple(transaction.id for transaction in portfolio.transactions) == transaction_order


def test_same_datetime_transactions_preserve_aggregate_relative_order() -> None:
    asset = make_asset(identity=1, symbol="STABLE")
    portfolio = make_portfolio(asset)
    for identity, transaction_type, quantity, price in (
        (1, TransactionType.BUY, "10", "100"),
        (2, TransactionType.SELL, "10", "130"),
        (3, TransactionType.BUY, "5", "80"),
    ):
        record(
            portfolio,
            identity=identity,
            asset=asset,
            transaction_type=transaction_type,
            quantity=quantity,
            price=price,
            date=NOW,
        )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="5",
        average_cost="80",
        cost_basis="400",
        realized_pnl="300",
    )


def test_result_and_collection_are_immutable_and_calculation_is_repeatable() -> None:
    asset = make_asset(identity=1, symbol="PURE")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="2",
        price="10",
    )
    transactions_before = tuple(portfolio.transactions)
    assets_before = tuple(portfolio.assets)
    calculator = PortfolioPositionCalculator()

    first = calculator.calculate(portfolio)
    second = calculator.calculate(portfolio)

    assert first == second
    assert first is not second
    assert isinstance(first, tuple)
    with pytest.raises(FrozenInstanceError):
        setattr(first[0], "quantity", Decimal("99"))
    assert tuple(portfolio.transactions) == transactions_before
    assert tuple(portfolio.assets) == assets_before


def test_buy_commission_and_tax_are_capitalized() -> None:
    asset = make_asset(identity=1, symbol="BUYFEE")
    portfolio = make_portfolio(asset)
    transaction = record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
        commission="10",
        tax="5",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="10",
        average_cost="101.5",
        cost_basis="1015",
        realized_pnl="0",
    )
    assert transaction.commission == Decimal("10")
    assert transaction.tax == Decimal("5")


def test_multiple_buys_include_every_charge_in_weighted_average() -> None:
    asset = make_asset(identity=1, symbol="MULTIFEE")
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
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="5",
        price="130",
        commission="5",
        tax="5",
    )

    position = calculate_one(portfolio)

    assert position.quantity == Decimal("15")
    assert position.cost_basis == Decimal("1675")
    assert position.average_cost == Decimal("1675") / Decimal("15")
    assert position.realized_pnl == Decimal("0")


def test_sell_commission_and_tax_reduce_realized_pnl() -> None:
    asset = make_asset(identity=1, symbol="SELLFEE")
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

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="6",
        average_cost="100",
        cost_basis="600",
        realized_pnl="112",
    )


def test_buy_and_sell_charges_apply_to_their_distinct_economic_formulas() -> None:
    asset = make_asset(identity=1, symbol="BOTHFEE")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
        commission="10",
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

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="6",
        average_cost="101",
        cost_basis="606",
        realized_pnl="108",
    )


def test_full_close_resets_exactly_and_realized_pnl_includes_sell_charges() -> None:
    asset = make_asset(identity=1, symbol="CLOSEFEE")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="100",
        commission="10",
    )
    record(
        portfolio,
        identity=2,
        asset=asset,
        transaction_type=TransactionType.SELL,
        quantity="10",
        price="130",
        commission="5",
        tax="3",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="0",
        average_cost="0",
        cost_basis="0",
        realized_pnl="282",
    )


def test_close_and_reopen_preserve_realized_pnl_and_capitalize_new_charges() -> None:
    asset = make_asset(identity=1, symbol="REOPENFEE")
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
        commission="5",
    )
    record(
        portfolio,
        identity=3,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="5",
        price="80",
        commission="4",
        tax="1",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="5",
        average_cost="81",
        cost_basis="405",
        realized_pnl="295",
    )


def test_zero_price_buy_without_charges_has_zero_basis_without_division_error() -> None:
    asset = make_asset(identity=1, symbol="ZEROBUY")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="0",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="10",
        average_cost="0",
        cost_basis="0",
        realized_pnl="0",
    )


def test_zero_price_buy_with_charges_uses_charges_as_cost_basis() -> None:
    asset = make_asset(identity=1, symbol="ZEROFEE")
    portfolio = make_portfolio(asset)
    record(
        portfolio,
        identity=1,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="10",
        price="0",
        commission="10",
        tax="5",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="10",
        average_cost="1.5",
        cost_basis="15",
        realized_pnl="0",
    )


def test_zero_price_sell_uses_negative_net_proceeds_and_disposed_cost() -> None:
    asset = make_asset(identity=1, symbol="ZEROSELL")
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
        price="0",
        commission="5",
        tax="3",
    )

    position = calculate_one(portfolio)

    assert_position(
        position,
        asset_id=asset.id,
        quantity="6",
        average_cost="100",
        cost_basis="600",
        realized_pnl="-408",
    )


@pytest.mark.parametrize(
    ("transaction_type", "price"),
    [
        (TransactionType.DIVIDEND, "1"),
        (TransactionType.RIGHTS_ISSUE, "1"),
        (TransactionType.BONUS_ISSUE, "0"),
        (TransactionType.STOCK_SPLIT, "0"),
    ],
)
def test_unsupported_transaction_type_raises_with_exact_context(
    transaction_type: TransactionType,
    price: str,
) -> None:
    asset = make_asset(identity=1, symbol="UNSUPPORTED")
    portfolio = make_portfolio(asset)
    transaction = record(
        portfolio,
        identity=42,
        asset=asset,
        transaction_type=transaction_type,
        quantity="1",
        price=price,
    )

    with pytest.raises(UnsupportedTransactionTypeError) as raised:
        PortfolioPositionCalculator().calculate(portfolio)

    assert isinstance(raised.value, DomainError)
    assert raised.value.transaction_id is transaction.id
    assert raised.value.transaction_type is transaction_type
    assert transaction_type.value in str(raised.value)


def test_unsupported_type_after_valid_transactions_is_atomic_and_retains_no_state() -> None:
    asset = make_asset(identity=1, symbol="FAIL")
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
        transaction_type=TransactionType.DIVIDEND,
        quantity="10",
        price="1",
    )
    transactions_before = tuple(portfolio.transactions)
    assets_before = tuple(portfolio.assets)
    calculator = PortfolioPositionCalculator()

    with pytest.raises(UnsupportedTransactionTypeError):
        calculator.calculate(portfolio)

    valid_portfolio = make_portfolio(asset)
    record(
        valid_portfolio,
        identity=3,
        asset=asset,
        transaction_type=TransactionType.BUY,
        quantity="2",
        price="25",
    )
    assert_position(
        calculator.calculate(valid_portfolio)[0],
        asset_id=asset.id,
        quantity="2",
        average_cost="25",
        cost_basis="50",
        realized_pnl="0",
    )
    assert tuple(portfolio.transactions) == transactions_before
    assert tuple(portfolio.assets) == assets_before


def test_calculator_does_not_use_transaction_calculate_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = make_asset(identity=1, symbol="EXPLICIT")
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

    def reject_total_call(transaction: Transaction) -> Decimal:
        del transaction
        raise AssertionError("Position calculation must use explicit transaction formulas.")

    monkeypatch.setattr(Transaction, "calculate_total", reject_total_call)

    position = calculate_one(portfolio)

    assert position.cost_basis == Decimal("609")
    assert position.realized_pnl == Decimal("106")


def test_public_result_contract_and_domain_exports_are_exact() -> None:
    assert tuple(field.name for field in fields(AssetPosition)) == (
        "asset_id",
        "quantity",
        "average_cost",
        "cost_basis",
        "realized_pnl",
    )
    assert PortfolioPositionCalculator.__module__ == ("app.domain.services.position_calculator")


def test_position_engine_has_no_forbidden_architecture_dependencies() -> None:
    production_files = (
        CALCULATOR_MODULE,
        POSITION_MODULE,
        EXCEPTION_MODULE,
    )
    forbidden_imports = {
        "sqlalchemy",
        "sqlite3",
        "alembic",
        "PySide6",
        "app.application",
        "app.infrastructure",
        "app.ui",
        "app.repositories",
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
        "UnitOfWork",
        "Repository",
        "Session",
        "float(",
        ".quantize(",
        "calculate_total(",
        "market_price",
        "market_value",
        "unrealized",
        "total_return",
        "publish(",
        "._transactions",
        ".sort(",
        "sorted(",
    ):
        assert forbidden_token not in source
