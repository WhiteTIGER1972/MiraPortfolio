"""Synchronous, process-local domain-event delivery."""

from typing import cast

from app.application.events.handler import EventHandler
from app.application.events.publisher import EventPublisher
from app.domain.events import DomainEvent


class InProcessEventBus(EventPublisher):
    """Deliver events synchronously to exact-type handlers in registration order."""

    def __init__(self) -> None:
        self._handlers: dict[
            type[DomainEvent],
            list[EventHandler[DomainEvent]],
        ] = {}

    def register[EventT: DomainEvent](
        self,
        event_type: type[EventT],
        handler: EventHandler[EventT],
    ) -> None:
        """Register a handler once for one exact event type."""
        stored_handler = cast(EventHandler[DomainEvent], handler)
        handlers = self._handlers.setdefault(event_type, [])
        if stored_handler not in handlers:
            handlers.append(stored_handler)

    def unregister[EventT: DomainEvent](
        self,
        event_type: type[EventT],
        handler: EventHandler[EventT],
    ) -> None:
        """Remove a handler, doing nothing when it is not registered."""
        stored_handler = cast(EventHandler[DomainEvent], handler)
        handlers = self._handlers.get(event_type)
        if handlers is None:
            return

        try:
            handlers.remove(stored_handler)
        except ValueError:
            return

        if not handlers:
            del self._handlers[event_type]

    def publish(self, event: DomainEvent, /) -> None:
        """Deliver an event synchronously and propagate the first handler error."""
        for handler in tuple(self._handlers.get(type(event), ())):
            handler(event)

    def handlers_for[EventT: DomainEvent](
        self,
        event_type: type[EventT],
    ) -> tuple[EventHandler[EventT], ...]:
        """Return an immutable snapshot of handlers for an exact event type."""
        handlers = tuple(self._handlers.get(event_type, ()))
        return cast(tuple[EventHandler[EventT], ...], handlers)
