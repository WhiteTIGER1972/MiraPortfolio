"""Historical asset price domain entity."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.value_objects.currency import Currency


@dataclass(frozen=True, slots=True)
class PriceHistory:
    """Capture a timestamped market price for an asset."""

    asset_id: UUID
    price: Decimal
    currency: Currency
    observed_at: datetime
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        """Validate price values and normalize naive timestamps to UTC."""
        if self.price < 0:
            raise ValueError("Historical price cannot be negative.")
        if self.observed_at.tzinfo is None:
            object.__setattr__(self, "observed_at", self.observed_at.replace(tzinfo=UTC))
