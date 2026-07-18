"""Tests for the event-dispatch facade."""

from uuid import uuid4

import pytest

from app.application.events import EventDispatcher
from app.domain.events import DomainEvent, EventReasonCode, PortfolioUpdated


class RecordingPublisher:
    """Record events published by a dispatcher."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    def publish(self, event: DomainEvent, /) -> None:
        """Record the exact event instance."""
        self.events.append(event)


class FailingPublisher:
    """Raise one stable exception for propagation tests."""

    def __init__(self, error: RuntimeError) -> None:
        self.error = error

    def publish(self, _event: DomainEvent, /) -> None:
        """Raise the configured error."""
        raise self.error


def make_event() -> PortfolioUpdated:
    """Build an event for dispatcher tests."""
    return PortfolioUpdated(
        portfolio_id=uuid4(),
        reason=EventReasonCode.TRANSACTION_ADDED,
        source_event_id=uuid4(),
    )


def test_dispatcher_forwards_event_without_modification() -> None:
    """Dispatch forwards the same event instance to its publisher."""
    publisher = RecordingPublisher()
    dispatcher = EventDispatcher(publisher)
    event = make_event()

    dispatcher.dispatch(event)

    assert publisher.events == [event]
    assert publisher.events[0] is event


def test_publisher_exception_propagates_unchanged() -> None:
    """Dispatcher does not wrap publisher failures."""
    expected = RuntimeError("publisher failed")
    dispatcher = EventDispatcher(FailingPublisher(expected))

    with pytest.raises(RuntimeError) as raised:
        dispatcher.dispatch(make_event())

    assert raised.value is expected
