"""Portfolio valuation snapshot domain entity."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.value_objects.currency import Currency


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Capture an immutable point-in-time valuation of a portfolio."""

    portfolio_id: UUID
    total_value: Decimal
    currency: Currency
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        """Validate valuation values and normalize naive timestamps to UTC."""
        if self.total_value < 0:
            raise ValueError("Snapshot total value cannot be negative.")
        if self.captured_at.tzinfo is None:
            object.__setattr__(self, "captured_at", self.captured_at.replace(tzinfo=UTC))
