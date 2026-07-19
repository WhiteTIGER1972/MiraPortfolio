"""Focused tests for Portfolio aggregate transaction behavior."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.exceptions import TransactionNotFoundError
from app.domain.value_objects.currency import Currency

NOW = datetime(2026, 7, 19, 16, 15, tzinfo=UTC)


def make_asset(*, identity: int, symbol: str) -> Asset:
    return Asset(
        id=UUID(int=identity),
        symbol=symbol,
        name=f"{symbol} Asset",
        asset_type=AssetType.EQUITY,
        currency=Currency.TRY,
        created_at=NOW,
    )


def make_transaction(
    *,
    identity: int,
    asset_id: UUID,
    quantity: str,
) -> Transaction:
    return Transaction(
        id=UUID(int=identity),
        asset_id=asset_id,
        quantity=Decimal(quantity),
        price=Decimal("125.50"),
        transaction_type=TransactionType.BUY,
        date=NOW,
    )


def test_remove_transaction_removes_only_target_and_preserves_remaining_order() -> None:
    asset = make_asset(identity=1, symbol="ORDER")
    portfolio = Portfolio(name="Ordered", id=UUID(int=10), created_at=NOW)
    portfolio.add_asset(asset)
    first = make_transaction(identity=101, asset_id=asset.id, quantity="1")
    middle = make_transaction(identity=102, asset_id=asset.id, quantity="2")
    last = make_transaction(identity=103, asset_id=asset.id, quantity="3")
    for transaction in (first, middle, last):
        portfolio.record_transaction(transaction)
    assets_before = tuple(portfolio.assets)

    portfolio.remove_transaction(middle.id)

    assert tuple(portfolio.transactions) == (first, last)
    assert portfolio.transactions[0] is first
    assert portfolio.transactions[1] is last
    assert first.quantity == Decimal("1")
    assert last.quantity == Decimal("3")
    assert tuple(portfolio.assets) == assets_before


def test_remove_final_asset_transaction_preserves_assets_and_their_order() -> None:
    first_asset = make_asset(identity=1, symbol="FIRST")
    second_asset = make_asset(identity=2, symbol="SECOND")
    portfolio = Portfolio(name="Assets", id=UUID(int=20), created_at=NOW)
    portfolio.add_asset(first_asset)
    portfolio.add_asset(second_asset)
    transaction = make_transaction(
        identity=201,
        asset_id=first_asset.id,
        quantity="4",
    )
    portfolio.record_transaction(transaction)

    portfolio.remove_transaction(transaction.id)

    assert tuple(portfolio.transactions) == ()
    assert tuple(portfolio.assets) == (first_asset, second_asset)
    assert portfolio.assets[0] is first_asset
    assert portfolio.assets[1] is second_asset


def test_remove_unknown_transaction_is_atomic_and_reports_missing_identity() -> None:
    asset = make_asset(identity=1, symbol="ATOMIC")
    portfolio = Portfolio(name="Atomic", id=UUID(int=30), created_at=NOW)
    portfolio.add_asset(asset)
    transaction = make_transaction(identity=301, asset_id=asset.id, quantity="5")
    portfolio.record_transaction(transaction)
    transactions_before = tuple(portfolio.transactions)
    assets_before = tuple(portfolio.assets)
    unknown_id = UUID(int=999)

    with pytest.raises(TransactionNotFoundError) as raised:
        portfolio.remove_transaction(unknown_id)

    assert raised.value.transaction_id is unknown_id
    assert str(unknown_id) in str(raised.value)
    assert tuple(portfolio.transactions) == transactions_before
    assert tuple(portfolio.assets) == assets_before


def test_record_transaction_membership_and_ordering_behavior_is_unchanged() -> None:
    asset = make_asset(identity=1, symbol="MEMBER")
    other_asset = make_asset(identity=2, symbol="OUTSIDER")
    portfolio = Portfolio(name="Recording", id=UUID(int=40), created_at=NOW)
    portfolio.add_asset(asset)
    first = make_transaction(identity=401, asset_id=asset.id, quantity="6")
    second = make_transaction(identity=402, asset_id=asset.id, quantity="7")
    outsider = make_transaction(identity=403, asset_id=other_asset.id, quantity="8")

    portfolio.record_transaction(first)
    portfolio.record_transaction(second)

    assert tuple(portfolio.transactions) == (first, second)
    with pytest.raises(ValueError, match="must belong"):
        portfolio.record_transaction(outsider)
    assert tuple(portfolio.transactions) == (first, second)
