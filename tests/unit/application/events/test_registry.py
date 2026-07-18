"""Tests for explicit application event-handler composition."""

from decimal import Decimal
from uuid import uuid4

from app.application.events import (
    InProcessEventBus,
    register_application_event_handlers,
)
from app.domain.entities.transaction import TransactionType
from app.domain.events import (
    DashboardRefreshRequested,
    EventReasonCode,
    PortfolioUpdated,
    SnapshotCreated,
    TransactionAdded,
)
from app.domain.value_objects.currency import Currency


def make_transaction_added() -> TransactionAdded:
    """Build a valid source event for orchestration tests."""
    return TransactionAdded(
        transaction_id=uuid4(),
        portfolio_id=uuid4(),
        asset_id=uuid4(),
        transaction_type=TransactionType.BUY,
        quantity=Decimal("3"),
        unit_price=Decimal("12.75"),
        currency=Currency.EUR,
    )


def test_registry_has_no_effect_until_called() -> None:
    """A fresh bus has no production handlers from import side effects."""
    bus = InProcessEventBus()

    assert bus.handlers_for(TransactionAdded) == ()


def test_registered_transaction_handler_emits_portfolio_updated() -> None:
    """The implemented flow translates a transaction into a portfolio update."""
    bus = InProcessEventBus()
    portfolio_events: list[PortfolioUpdated] = []
    bus.register(PortfolioUpdated, portfolio_events.append)
    registrations = register_application_event_handlers(bus)
    transaction = make_transaction_added()

    bus.publish(transaction)

    assert bus.handlers_for(TransactionAdded) == (registrations.transaction_added,)
    assert len(portfolio_events) == 1
    portfolio_event = portfolio_events[0]
    assert portfolio_event.portfolio_id == transaction.portfolio_id
    assert portfolio_event.reason is EventReasonCode.TRANSACTION_ADDED
    assert portfolio_event.source_event_id == transaction.id


def test_registry_does_not_fabricate_snapshot_or_dashboard_events() -> None:
    """The initial flow stops before calculation- or UI-dependent events."""
    bus = InProcessEventBus()
    snapshots: list[SnapshotCreated] = []
    refresh_requests: list[DashboardRefreshRequested] = []
    bus.register(SnapshotCreated, snapshots.append)
    bus.register(DashboardRefreshRequested, refresh_requests.append)
    register_application_event_handlers(bus)

    bus.publish(make_transaction_added())

    assert snapshots == []
    assert refresh_requests == []


def test_repeated_registry_call_does_not_duplicate_equivalent_handler() -> None:
    """Explicit composition remains idempotent for the supplied bus."""
    bus = InProcessEventBus()

    register_application_event_handlers(bus)
    register_application_event_handlers(bus)

    assert len(bus.handlers_for(TransactionAdded)) == 1
