"""Publishing contract used by application event orchestration."""

from typing import Protocol

from app.domain.events import DomainEvent


class EventPublisher(Protocol):
    """Publish domain events without exposing a concrete transport."""

    def publish(self, event: DomainEvent, /) -> None:
        """Publish an event."""
