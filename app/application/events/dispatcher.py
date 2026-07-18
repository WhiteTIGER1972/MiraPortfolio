"""Application-facing domain-event dispatch facade."""

from dataclasses import dataclass

from app.application.events.publisher import EventPublisher
from app.domain.events import DomainEvent


@dataclass(frozen=True, slots=True)
class EventDispatcher:
    """Dispatch domain events through an injected publisher."""

    publisher: EventPublisher

    def dispatch(self, event: DomainEvent, /) -> None:
        """Dispatch an event through the configured publisher."""
        self.publisher.publish(event)
