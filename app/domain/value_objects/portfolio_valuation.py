"""Immutable portfolio valuation result values."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.domain.value_objects.currency import Currency


@dataclass(frozen=True, slots=True)
class ValuedAssetPosition:
    """Represent one transaction-derived position at a supplied current price."""

    asset_id: UUID
    currency: Currency
    quantity: Decimal
    average_cost: Decimal
    cost_basis: Decimal
    market_price: Decimal | None
    market_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal


@dataclass(frozen=True, slots=True)
class CurrencyValuation:
    """Aggregate valued positions that share one currency."""

    currency: Currency
    cost_basis: Decimal
    market_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    """Contain ordered per-asset and per-currency valuation results."""

    positions: tuple[ValuedAssetPosition, ...]
    currencies: tuple[CurrencyValuation, ...]


__all__ = [
    "CurrencyValuation",
    "PortfolioValuation",
    "ValuedAssetPosition",
]
