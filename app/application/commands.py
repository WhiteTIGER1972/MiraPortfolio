"""Immutable input DTOs for portfolio application use cases."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CreatePortfolioCommand:
    """Request creation of a portfolio."""

    portfolio_name: str


@dataclass(frozen=True, slots=True)
class BuyAssetCommand:
    """Request an asset purchase for a portfolio."""

    portfolio_id: UUID
    symbol: str
    quantity: Decimal
    unit_price: Decimal
    trade_datetime: datetime


@dataclass(frozen=True, slots=True)
class SellAssetCommand:
    """Request an asset sale for a portfolio."""

    portfolio_id: UUID
    symbol: str
    quantity: Decimal
    unit_price: Decimal
    trade_datetime: datetime


@dataclass(frozen=True, slots=True)
class DeleteTransactionCommand:
    """Request removal of a transaction from a portfolio."""

    portfolio_id: UUID
    transaction_id: UUID


__all__ = [
    "BuyAssetCommand",
    "CreatePortfolioCommand",
    "DeleteTransactionCommand",
    "SellAssetCommand",
]
