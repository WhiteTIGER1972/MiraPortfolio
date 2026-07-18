"""Application-level contract for synchronous domain-event handlers."""

from typing import Protocol

from app.domain.events import DomainEvent


class EventHandler[EventT: DomainEvent](Protocol):
    """Handle one concrete domain-event type."""

    def __call__(self, event: EventT, /) -> None:
        """Handle an event."""
