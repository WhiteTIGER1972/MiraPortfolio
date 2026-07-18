"""Event raised when a valid portfolio valuation snapshot has been created."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.domain.events._validation import (
    validate_decimal,
    validate_enum,
    validate_utc_timestamp,
    validate_uuid,
)
from app.domain.events.base import DomainEvent
from app.domain.value_objects.currency import Currency


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotCreated(DomainEvent):
    """Describe a completed portfolio snapshot without persistence references."""

    snapshot_id: UUID
    portfolio_id: UUID
    total_value: Decimal
    currency: Currency
    source_event_id: UUID

    def __post_init__(self) -> None:
        """Validate identifiers, valuation, currency, and event metadata."""
        validate_uuid(self.id, "id")
        validate_uuid(self.snapshot_id, "snapshot_id")
        validate_uuid(self.portfolio_id, "portfolio_id")
        validate_uuid(self.source_event_id, "source_event_id")
        validate_decimal(self.total_value, "total_value")
        validate_enum(self.currency, Currency, "currency")
        validate_utc_timestamp(self.occurred_at, "occurred_at")
