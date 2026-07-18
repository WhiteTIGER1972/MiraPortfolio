"""Unit tests for explicit domain and ORM mappings."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.price_history import PriceHistory
from app.domain.entities.snapshot import Snapshot
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.value_objects.currency import Currency
from app.infrastructure.persistence.sqlalchemy.mappers import (
    asset_to_domain,
    asset_to_model,
    portfolio_to_domain,
    portfolio_to_model,
    price_history_to_domain,
    price_history_to_model,
    snapshot_to_domain,
    snapshot_to_model,
    transaction_to_domain,
    transaction_to_model,
)
from app.infrastructure.persistence.sqlalchemy.models import TransactionModel

NOW = datetime(2026, 7, 18, 12, 30, 45, 123456, tzinfo=UTC)


def make_asset() -> Asset:
    return Asset(
        symbol="MIRA",
        name="Mira Equity",
        asset_type=AssetType.EQUITY,
        currency=Currency.TRY,
        id=uuid4(),
        is_active=False,
        created_at=NOW,
    )


def make_transaction(asset_id: UUID) -> Transaction:
    return Transaction(
        asset_id=asset_id,
        quantity=Decimal("123456789.123400"),
        price=Decimal("0.00000000000000012345"),
        transaction_type=TransactionType.BUY,
        commission=Decimal("1.2300"),
        tax=Decimal("0.0400"),
        date=NOW,
        id=uuid4(),
    )


def test_asset_mapping_round_trip_preserves_fields_and_source() -> None:
    asset = make_asset()
    original = deepcopy(asset)

    reconstructed = asset_to_domain(asset_to_model(asset))

    assert reconstructed == asset
    assert asset == original
    assert reconstructed is not asset


def test_transaction_mapping_round_trip_preserves_exact_values() -> None:
    asset_id = uuid4()
    transaction = make_transaction(asset_id)
    original = deepcopy(transaction)

    model = transaction_to_model(
        transaction,
        portfolio_id=uuid4(),
        position=3,
    )
    reconstructed = transaction_to_domain(model)

    assert reconstructed == transaction
    assert str(reconstructed.quantity) == "123456789.123400"
    assert str(reconstructed.price) == "1.2345E-16"
    assert str(reconstructed.commission) == "1.2300"
    assert reconstructed.date.tzinfo is UTC
    assert transaction == original


def test_portfolio_mapping_round_trip_uses_public_aggregate_methods() -> None:
    first_asset = make_asset()
    second_asset = Asset(
        symbol="SECOND",
        name="Second Asset",
        asset_type=AssetType.ETF,
        currency=Currency.USD,
        id=uuid4(),
        created_at=NOW,
    )
    first_transaction = make_transaction(first_asset.id)
    second_transaction = make_transaction(second_asset.id)
    portfolio = Portfolio(
        name=" Long Term ",
        base_currency=Currency.TRY,
        id=uuid4(),
        is_archived=True,
        created_at=NOW,
    )
    portfolio.add_asset(first_asset)
    portfolio.add_asset(second_asset)
    portfolio.record_transaction(first_transaction)
    portfolio.record_transaction(second_transaction)
    original = deepcopy(portfolio)

    model = portfolio_to_model(portfolio)
    for link, asset in zip(model.asset_links, portfolio.assets, strict=True):
        link.asset = asset_to_model(asset)
    reconstructed = portfolio_to_domain(model)

    assert reconstructed == portfolio
    assert [asset.id for asset in reconstructed.assets] == [
        first_asset.id,
        second_asset.id,
    ]
    assert [transaction.id for transaction in reconstructed.transactions] == [
        first_transaction.id,
        second_transaction.id,
    ]
    assert isinstance(reconstructed, Portfolio)
    assert portfolio == original


def test_price_history_mapping_round_trip_preserves_decimal_and_utc() -> None:
    history = PriceHistory(
        asset_id=uuid4(),
        price=Decimal("987654321.000000000123400"),
        currency=Currency.USD,
        observed_at=NOW,
        id=uuid4(),
    )

    reconstructed = price_history_to_domain(price_history_to_model(history))

    assert reconstructed == history
    assert str(reconstructed.price) == "987654321.000000000123400"
    assert reconstructed.observed_at.tzinfo is UTC


def test_snapshot_mapping_round_trip_preserves_decimal_and_utc() -> None:
    snapshot = Snapshot(
        portfolio_id=uuid4(),
        total_value=Decimal("12345678901234567890.1200"),
        currency=Currency.EUR,
        captured_at=NOW,
        id=uuid4(),
    )

    reconstructed = snapshot_to_domain(snapshot_to_model(snapshot))

    assert reconstructed == snapshot
    assert str(reconstructed.total_value) == "12345678901234567890.1200"
    assert reconstructed.captured_at.tzinfo is UTC


def test_mappers_reject_nil_domain_identifiers() -> None:
    asset = make_asset()
    asset.id = UUID(int=0)
    portfolio = Portfolio(name="Nil Portfolio", id=UUID(int=0), created_at=NOW)

    with pytest.raises(ValueError, match="non-nil UUID"):
        asset_to_model(asset)
    with pytest.raises(ValueError, match="non-nil UUID"):
        portfolio_to_model(portfolio)


def test_mapper_rejects_naive_domain_timestamp() -> None:
    asset = make_asset()
    asset.created_at = datetime(2026, 7, 18, 12, 30)

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        asset_to_model(asset)


def test_mapper_rejects_non_utc_domain_timestamp() -> None:
    asset = make_asset()
    asset.created_at = datetime(
        2026,
        7,
        18,
        12,
        30,
        tzinfo=timezone(timedelta(hours=3)),
    )

    with pytest.raises(ValueError, match="must use UTC"):
        asset_to_model(asset)


def test_portfolio_mapper_revalidates_transaction_membership() -> None:
    portfolio = Portfolio(name="Invalid", created_at=NOW)
    portfolio.transactions.append(make_transaction(uuid4()))

    with pytest.raises(ValueError, match="must belong"):
        portfolio_to_model(portfolio)


def test_orm_to_domain_mapping_executes_transaction_invariants() -> None:
    model = TransactionModel(
        id=uuid4(),
        portfolio_id=uuid4(),
        asset_id=uuid4(),
        position=0,
        quantity=Decimal("0"),
        price=Decimal("1"),
        transaction_type=TransactionType.BUY.value,
        commission=Decimal("0"),
        tax=Decimal("0"),
        date=NOW,
    )

    with pytest.raises(ValueError, match="quantity must be positive"):
        transaction_to_domain(model)
