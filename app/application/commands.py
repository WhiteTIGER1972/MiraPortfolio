"""Immutable input DTOs for portfolio application use cases."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency


@dataclass(frozen=True, slots=True)
class CreateAssetCommand:
    """Request creation of an asset."""

    symbol: str
    name: str
    asset_type: AssetType
    currency: Currency


@dataclass(frozen=True, slots=True)
class CreatePortfolioCommand:
    """Request creation of a portfolio."""

    portfolio_name: str


@dataclass(frozen=True, slots=True)
class BuyAssetCommand:
    """Request an asset purchase for a portfolio."""

    portfolio_id: UUID
    asset_id: UUID
    quantity: Decimal
    unit_price: Decimal
    trade_datetime: datetime
    commission: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class SellAssetCommand:
    """Request an asset sale for a portfolio."""

    portfolio_id: UUID
    asset_id: UUID
    quantity: Decimal
    unit_price: Decimal
    trade_datetime: datetime
    commission: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class DeleteTransactionCommand:
    """Request removal of a transaction from a portfolio."""

    portfolio_id: UUID
    transaction_id: UUID


__all__ = [
    "BuyAssetCommand",
    "CreateAssetCommand",
    "CreatePortfolioCommand",
    "DeleteTransactionCommand",
    "SellAssetCommand",
]
