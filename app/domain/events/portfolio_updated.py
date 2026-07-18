"""Event describing a portfolio state change that requires orchestration."""

from dataclasses import dataclass
from uuid import UUID

from app.domain.events._validation import (
    validate_enum,
    validate_utc_timestamp,
    validate_uuid,
)
from app.domain.events.base import DomainEvent
from app.domain.events.reason import EventReasonCode


@dataclass(frozen=True, slots=True, kw_only=True)
class PortfolioUpdated(DomainEvent):
    """Describe why a portfolio changed and which event caused the change."""

    portfolio_id: UUID
    reason: EventReasonCode
    source_event_id: UUID

    def __post_init__(self) -> None:
        """Validate identifiers, reason, and event metadata."""
        validate_uuid(self.id, "id")
        validate_uuid(self.portfolio_id, "portfolio_id")
        validate_uuid(self.source_event_id, "source_event_id")
        validate_enum(self.reason, EventReasonCode, "reason")
        validate_utc_timestamp(self.occurred_at, "occurred_at")
