"""Tests for deterministic in-process event delivery."""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.events import InProcessEventBus
from app.domain.entities.transaction import TransactionType
from app.domain.events import EventReasonCode, PortfolioUpdated, TransactionAdded
from app.domain.value_objects.currency import Currency


def make_portfolio_updated() -> PortfolioUpdated:
    """Build a portfolio event for bus tests."""
    return PortfolioUpdated(
        portfolio_id=uuid4(),
        reason=EventReasonCode.TRANSACTION_ADDED,
        source_event_id=uuid4(),
    )


def make_transaction_added() -> TransactionAdded:
    """Build a transaction event for exact-type dispatch tests."""
    return TransactionAdded(
        transaction_id=uuid4(),
        portfolio_id=uuid4(),
        asset_id=uuid4(),
        transaction_type=TransactionType.BUY,
        quantity=Decimal("2.5"),
        unit_price=Decimal("10.25"),
        currency=Currency.TRY,
    )


def test_registered_handler_receives_same_event() -> None:
    """Publishing calls a registered exact-type handler."""
    bus = InProcessEventBus()
    received: list[PortfolioUpdated] = []
    bus.register(PortfolioUpdated, received.append)
    event = make_portfolio_updated()

    bus.publish(event)

    assert received == [event]
    assert received[0] is event


def test_multiple_handlers_run_in_registration_order() -> None:
    """Handlers run once in deterministic registration order."""
    bus = InProcessEventBus()
    calls: list[str] = []

    def first(event: PortfolioUpdated) -> None:
        calls.append(f"first:{event.reason}")

    def second(event: PortfolioUpdated) -> None:
        calls.append(f"second:{event.reason}")

    bus.register(PortfolioUpdated, first)
    bus.register(PortfolioUpdated, second)

    bus.publish(make_portfolio_updated())

    assert calls == ["first:transaction_added", "second:transaction_added"]


def test_duplicate_registration_is_ignored() -> None:
    """The same handler is registered at most once per event type."""
    bus = InProcessEventBus()
    received: list[PortfolioUpdated] = []
    handler = received.append

    bus.register(PortfolioUpdated, handler)
    bus.register(PortfolioUpdated, handler)
    bus.publish(make_portfolio_updated())

    assert len(received) == 1
    assert bus.handlers_for(PortfolioUpdated) == (handler,)


def test_handler_can_be_unregistered() -> None:
    """An unregistered handler is no longer invoked."""
    bus = InProcessEventBus()
    received: list[PortfolioUpdated] = []
    handler = received.append
    bus.register(PortfolioUpdated, handler)

    bus.unregister(PortfolioUpdated, handler)
    bus.publish(make_portfolio_updated())

    assert received == []
    assert bus.handlers_for(PortfolioUpdated) == ()


def test_unregistering_unknown_handler_is_a_no_op() -> None:
    """Removing absent registrations does not fail."""
    bus = InProcessEventBus()
    received: list[PortfolioUpdated] = []

    bus.unregister(PortfolioUpdated, received.append)

    assert bus.handlers_for(PortfolioUpdated) == ()


def test_publishing_without_handlers_is_a_no_op() -> None:
    """Events need not have a subscriber."""
    InProcessEventBus().publish(make_portfolio_updated())


def test_handlers_for_another_event_type_are_not_invoked() -> None:
    """Dispatch matches the concrete event type exactly."""
    bus = InProcessEventBus()
    received: list[TransactionAdded] = []
    bus.register(TransactionAdded, received.append)

    bus.publish(make_portfolio_updated())

    assert received == []


def test_handler_exception_propagates_unchanged() -> None:
    """The bus exposes the original handler error without wrapping it."""
    bus = InProcessEventBus()
    expected = RuntimeError("handler failed")

    def fail(_event: PortfolioUpdated) -> None:
        raise expected

    bus.register(PortfolioUpdated, fail)

    with pytest.raises(RuntimeError) as raised:
        bus.publish(make_portfolio_updated())

    assert raised.value is expected


def test_dispatch_stops_after_first_failing_handler() -> None:
    """Fail-fast delivery does not call later handlers."""
    bus = InProcessEventBus()
    calls: list[str] = []

    def fail(_event: PortfolioUpdated) -> None:
        calls.append("fail")
        raise RuntimeError("handler failed")

    def after_failure(_event: PortfolioUpdated) -> None:
        calls.append("after")

    bus.register(PortfolioUpdated, fail)
    bus.register(PortfolioUpdated, after_failure)

    with pytest.raises(RuntimeError, match="handler failed"):
        bus.publish(make_portfolio_updated())

    assert calls == ["fail"]


def test_separate_bus_instances_do_not_share_handlers() -> None:
    """Each bus owns an independent handler registry."""
    first_bus = InProcessEventBus()
    second_bus = InProcessEventBus()
    received: list[PortfolioUpdated] = []
    first_bus.register(PortfolioUpdated, received.append)

    second_bus.publish(make_portfolio_updated())

    assert received == []
    assert second_bus.handlers_for(PortfolioUpdated) == ()


def test_handler_collection_is_an_immutable_snapshot() -> None:
    """Callers cannot mutate the bus through its handler inspection API."""
    bus = InProcessEventBus()
    received: list[PortfolioUpdated] = []
    handler = received.append
    bus.register(PortfolioUpdated, handler)

    snapshot = bus.handlers_for(PortfolioUpdated)
    bus.unregister(PortfolioUpdated, handler)

    assert isinstance(snapshot, tuple)
    assert snapshot == (handler,)
    assert bus.handlers_for(PortfolioUpdated) == ()


def test_bus_can_publish_different_supported_event_instances() -> None:
    """Publishing remains deterministic across distinct concrete event types."""
    bus = InProcessEventBus()
    transactions: list[TransactionAdded] = []
    portfolios: list[PortfolioUpdated] = []
    bus.register(TransactionAdded, transactions.append)
    bus.register(PortfolioUpdated, portfolios.append)
    transaction = make_transaction_added()
    portfolio = make_portfolio_updated()

    bus.publish(transaction)
    bus.publish(portfolio)

    assert transactions == [transaction]
    assert portfolios == [portfolio]
