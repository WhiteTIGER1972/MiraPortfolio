"""Investment asset domain entity."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.domain.value_objects.currency import Currency


class AssetType(StrEnum):
    """Supported categories of investable assets."""

    EQUITY = "equity"
    FUND = "fund"
    ETF = "etf"
    BOND = "bond"
    CRYPTO = "crypto"
    CASH = "cash"


@dataclass(slots=True)
class Asset:
    """Represent an instrument that can be held in a portfolio.

    Args:
        symbol: Exchange or provider symbol for the instrument.
        name: Human-readable instrument name.
        asset_type: Investment category.
        currency: Currency used for pricing and settlement.
    """

    symbol: str
    name: str
    asset_type: AssetType
    currency: Currency
    id: UUID = field(default_factory=uuid4)
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        """Validate required instrument attributes."""
        if not self.symbol.strip():
            raise ValueError("Asset symbol cannot be empty.")
        if not self.name.strip():
            raise ValueError("Asset name cannot be empty.")
        self.symbol = self.symbol.upper().strip()
        self.name = self.name.strip()
