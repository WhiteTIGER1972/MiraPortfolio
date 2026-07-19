"""Immutable transaction-derived asset position values."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AssetPosition:
    """Represent the trading position derived from one asset's transactions."""

    asset_id: UUID
    quantity: Decimal
    average_cost: Decimal
    cost_basis: Decimal
    realized_pnl: Decimal


__all__ = ["AssetPosition"]
