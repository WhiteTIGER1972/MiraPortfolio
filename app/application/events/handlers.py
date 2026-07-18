"""Initial application event handlers."""

from dataclasses import dataclass

from app.application.events.dispatcher import EventDispatcher
from app.domain.events import EventReasonCode, PortfolioUpdated, TransactionAdded


@dataclass(frozen=True, slots=True)
class TransactionAddedHandler:
    """Translate an accepted transaction into a portfolio update event."""

    dispatcher: EventDispatcher

    def __call__(self, event: TransactionAdded, /) -> None:
        """Emit the next safe orchestration event."""
        self.dispatcher.dispatch(
            PortfolioUpdated(
                portfolio_id=event.portfolio_id,
                reason=EventReasonCode.TRANSACTION_ADDED,
                source_event_id=event.id,
            )
        )
