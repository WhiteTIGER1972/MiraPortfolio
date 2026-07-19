"""Immutable output DTOs for portfolio application use cases."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.domain.entities.asset import AssetType
from app.domain.entities.transaction import TransactionType
from app.domain.value_objects.currency import Currency


@dataclass(frozen=True, slots=True)
class AssetView:
    """Present descriptive Asset data without calculated values."""

    id: UUID
    symbol: str
    name: str
    asset_type: AssetType
    currency: Currency
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MarketPriceView:
    """Present one persisted market price without source or freshness policy."""

    id: UUID
    asset_id: UUID
    price: Decimal
    currency: Currency
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ValuedAssetPositionView:
    """Combine descriptive Asset data with calculated position valuation."""

    asset_id: UUID
    symbol: str
    name: str
    asset_type: AssetType
    currency: Currency
    quantity: Decimal
    average_cost: Decimal
    cost_basis: Decimal
    market_price: Decimal | None
    price_observed_at: datetime | None
    market_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal


@dataclass(frozen=True, slots=True)
class CurrencyValuationView:
    """Present one exact per-currency valuation bucket."""

    currency: Currency
    cost_basis: Decimal
    market_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    """Present the existing summary fields of a portfolio."""

    id: UUID
    name: str
    base_currency: Currency
    is_archived: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TransactionView:
    """Present an existing portfolio transaction without derived values."""

    id: UUID
    asset_id: UUID
    quantity: Decimal
    price: Decimal
    transaction_type: TransactionType
    commission: Decimal
    tax: Decimal
    date: datetime


@dataclass(frozen=True, slots=True)
class AssetPositionView:
    """Present an asset held by a portfolio without calculated position metrics."""

    id: UUID
    symbol: str
    name: str
    asset_type: AssetType
    currency: Currency
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PortfolioDetails:
    """Present the existing fields of a portfolio aggregate."""

    id: UUID
    name: str
    base_currency: Currency
    assets: tuple[AssetPositionView, ...]
    transactions: tuple[TransactionView, ...]
    is_archived: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PortfolioDashboard:
    """Present Portfolio details with immutable position and currency valuations."""

    portfolio: PortfolioDetails
    positions: tuple[ValuedAssetPositionView, ...]
    currencies: tuple[CurrencyValuationView, ...]


__all__ = [
    "AssetPositionView",
    "AssetView",
    "CurrencyValuationView",
    "MarketPriceView",
    "PortfolioDashboard",
    "PortfolioDetails",
    "PortfolioSummary",
    "TransactionView",
    "ValuedAssetPositionView",
]
