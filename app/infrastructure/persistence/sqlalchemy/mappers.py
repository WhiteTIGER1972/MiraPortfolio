"""Explicit mappings between domain entities and SQLAlchemy models."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.price_history import PriceHistory
from app.domain.entities.snapshot import Snapshot
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.value_objects.currency import Currency
from app.infrastructure.persistence.sqlalchemy.models import (
    AssetModel,
    PortfolioAssetModel,
    PortfolioModel,
    PriceHistoryModel,
    SnapshotModel,
    TransactionModel,
)


def _require_uuid(value: UUID, field_name: str) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{field_name} must be a non-nil UUID.")
    return value


def _require_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC.")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC.")
    return value


def _require_decimal(value: Decimal, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal.")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite.")
    return value


def asset_to_model(entity: Asset) -> AssetModel:
    """Map an Asset without mutating it."""
    return AssetModel(
        id=_require_uuid(entity.id, "asset.id"),
        symbol=entity.symbol,
        name=entity.name,
        asset_type=entity.asset_type.value,
        currency=entity.currency.value,
        is_active=entity.is_active,
        created_at=_require_utc(entity.created_at, "asset.created_at"),
    )


def asset_to_domain(model: AssetModel) -> Asset:
    """Reconstruct an Asset through its public constructor."""
    return Asset(
        symbol=model.symbol,
        name=model.name,
        asset_type=AssetType(model.asset_type),
        currency=Currency(model.currency),
        id=_require_uuid(model.id, "asset.id"),
        is_active=model.is_active,
        created_at=_require_utc(model.created_at, "asset.created_at"),
    )


def transaction_to_model(
    entity: Transaction,
    *,
    portfolio_id: UUID,
    position: int,
) -> TransactionModel:
    """Map a portfolio-owned Transaction without fabricating domain fields."""
    return TransactionModel(
        id=_require_uuid(entity.id, "transaction.id"),
        portfolio_id=_require_uuid(portfolio_id, "portfolio.id"),
        asset_id=_require_uuid(entity.asset_id, "transaction.asset_id"),
        position=position,
        quantity=_require_decimal(entity.quantity, "transaction.quantity"),
        price=_require_decimal(entity.price, "transaction.price"),
        transaction_type=entity.transaction_type.value,
        commission=_require_decimal(entity.commission, "transaction.commission"),
        tax=_require_decimal(entity.tax, "transaction.tax"),
        date=_require_utc(entity.date, "transaction.date"),
    )


def transaction_to_domain(model: TransactionModel) -> Transaction:
    """Reconstruct a Transaction through its validating constructor."""
    return Transaction(
        asset_id=_require_uuid(model.asset_id, "transaction.asset_id"),
        quantity=_require_decimal(model.quantity, "transaction.quantity"),
        price=_require_decimal(model.price, "transaction.price"),
        transaction_type=TransactionType(model.transaction_type),
        commission=_require_decimal(model.commission, "transaction.commission"),
        tax=_require_decimal(model.tax, "transaction.tax"),
        date=_require_utc(model.date, "transaction.date"),
        id=_require_uuid(model.id, "transaction.id"),
    )


def portfolio_to_model(entity: Portfolio) -> PortfolioModel:
    """Map a Portfolio aggregate while preserving member ordering."""
    portfolio_id = _require_uuid(entity.id, "portfolio.id")
    asset_ids = {_require_uuid(asset.id, "asset.id") for asset in entity.assets}
    for transaction in entity.transactions:
        if transaction.asset_id not in asset_ids:
            raise ValueError("Transaction asset must belong to the portfolio.")

    return PortfolioModel(
        id=portfolio_id,
        name=entity.name,
        base_currency=entity.base_currency.value,
        is_archived=entity.is_archived,
        created_at=_require_utc(entity.created_at, "portfolio.created_at"),
        asset_links=[
            PortfolioAssetModel(
                portfolio_id=portfolio_id,
                asset_id=asset.id,
                position=position,
            )
            for position, asset in enumerate(entity.assets)
        ],
        transactions=[
            transaction_to_model(
                transaction,
                portfolio_id=portfolio_id,
                position=position,
            )
            for position, transaction in enumerate(entity.transactions)
        ],
    )


def portfolio_to_domain(model: PortfolioModel) -> Portfolio:
    """Reconstruct a Portfolio through public constructors and aggregate methods."""
    portfolio = Portfolio(
        name=model.name,
        base_currency=Currency(model.base_currency),
        id=_require_uuid(model.id, "portfolio.id"),
        is_archived=model.is_archived,
        created_at=_require_utc(model.created_at, "portfolio.created_at"),
    )
    for link in model.asset_links:
        portfolio.add_asset(asset_to_domain(link.asset))
    for transaction_model in model.transactions:
        portfolio.record_transaction(transaction_to_domain(transaction_model))
    return portfolio


def price_history_to_model(entity: PriceHistory) -> PriceHistoryModel:
    """Map PriceHistory without mutating the immutable source entity."""
    return PriceHistoryModel(
        id=_require_uuid(entity.id, "price_history.id"),
        asset_id=_require_uuid(entity.asset_id, "price_history.asset_id"),
        price=_require_decimal(entity.price, "price_history.price"),
        currency=entity.currency.value,
        observed_at=_require_utc(entity.observed_at, "price_history.observed_at"),
    )


def price_history_to_domain(model: PriceHistoryModel) -> PriceHistory:
    """Reconstruct PriceHistory through its validating constructor."""
    return PriceHistory(
        asset_id=_require_uuid(model.asset_id, "price_history.asset_id"),
        price=_require_decimal(model.price, "price_history.price"),
        currency=Currency(model.currency),
        observed_at=_require_utc(model.observed_at, "price_history.observed_at"),
        id=_require_uuid(model.id, "price_history.id"),
    )


def snapshot_to_model(entity: Snapshot) -> SnapshotModel:
    """Map an immutable Snapshot without mutating it."""
    return SnapshotModel(
        id=_require_uuid(entity.id, "snapshot.id"),
        portfolio_id=_require_uuid(entity.portfolio_id, "snapshot.portfolio_id"),
        total_value=_require_decimal(entity.total_value, "snapshot.total_value"),
        currency=entity.currency.value,
        captured_at=_require_utc(entity.captured_at, "snapshot.captured_at"),
    )


def snapshot_to_domain(model: SnapshotModel) -> Snapshot:
    """Reconstruct a Snapshot through its validating constructor."""
    return Snapshot(
        portfolio_id=_require_uuid(model.portfolio_id, "snapshot.portfolio_id"),
        total_value=_require_decimal(model.total_value, "snapshot.total_value"),
        currency=Currency(model.currency),
        captured_at=_require_utc(model.captured_at, "snapshot.captured_at"),
        id=_require_uuid(model.id, "snapshot.id"),
    )
