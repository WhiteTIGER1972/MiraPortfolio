"""Explicit application event-handler registration."""

from dataclasses import dataclass

from app.application.events.bus import InProcessEventBus
from app.application.events.dispatcher import EventDispatcher
from app.application.events.handlers import TransactionAddedHandler
from app.domain.events import TransactionAdded


@dataclass(frozen=True, slots=True)
class ApplicationEventRegistrations:
    """References to the handlers registered for one application instance."""

    transaction_added: TransactionAddedHandler


def register_application_event_handlers(
    event_bus: InProcessEventBus,
) -> ApplicationEventRegistrations:
    """Register the implemented application event flow on an explicit bus."""
    dispatcher = EventDispatcher(event_bus)
    transaction_added_handler = TransactionAddedHandler(dispatcher)
    event_bus.register(TransactionAdded, transaction_added_handler)
    return ApplicationEventRegistrations(
        transaction_added=transaction_added_handler,
    )
