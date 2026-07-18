"""Tests for concrete immutable domain events."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import get_type_hints
from uuid import UUID, uuid4

import pytest

from app.domain.entities.transaction import TransactionType
from app.domain.events import (
    DashboardRefreshRequested,
    DomainEvent,
    EventReasonCode,
    PortfolioUpdated,
    SnapshotCreated,
    TransactionAdded,
)
from app.domain.value_objects.currency import Currency


def make_transaction_added(**overrides: object) -> TransactionAdded:
    """Build a valid transaction event with optional test overrides."""
    values: dict[str, object] = {
        "transaction_id": uuid4(),
        "portfolio_id": uuid4(),
        "asset_id": uuid4(),
        "transaction_type": TransactionType.BUY,
        "quantity": Decimal("0.1234567890123456789"),
        "unit_price": Decimal("19.9900000000000000001"),
        "currency": Currency.TRY,
    }
    values.update(overrides)
    return TransactionAdded(**values)  # type: ignore[arg-type]


def make_portfolio_updated(**overrides: object) -> PortfolioUpdated:
    """Build a valid portfolio event with optional test overrides."""
    values: dict[str, object] = {
        "portfolio_id": uuid4(),
        "reason": EventReasonCode.TRANSACTION_ADDED,
        "source_event_id": uuid4(),
    }
    values.update(overrides)
    return PortfolioUpdated(**values)  # type: ignore[arg-type]


def make_snapshot_created(**overrides: object) -> SnapshotCreated:
    """Build a valid snapshot event with optional test overrides."""
    values: dict[str, object] = {
        "snapshot_id": uuid4(),
        "portfolio_id": uuid4(),
        "total_value": Decimal("1234.567890123456789"),
        "currency": Currency.USD,
        "source_event_id": uuid4(),
    }
    values.update(overrides)
    return SnapshotCreated(**values)  # type: ignore[arg-type]


def make_dashboard_refresh_requested(
    **overrides: object,
) -> DashboardRefreshRequested:
    """Build a valid dashboard-refresh event with optional test overrides."""
    values: dict[str, object] = {
        "portfolio_id": uuid4(),
        "reason": EventReasonCode.SNAPSHOT_CREATED,
        "source_event_id": uuid4(),
    }
    values.update(overrides)
    return DashboardRefreshRequested(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "event",
    [
        make_transaction_added(),
        make_portfolio_updated(),
        make_snapshot_created(),
        make_dashboard_refresh_requested(),
    ],
)
def test_concrete_events_are_immutable(event: DomainEvent) -> None:
    """Concrete event fields cannot be reassigned."""
    with pytest.raises(FrozenInstanceError):
        event.occurred_at = datetime.now(UTC)  # type: ignore[misc]


@pytest.mark.parametrize(
    "event",
    [
        make_transaction_added(),
        make_portfolio_updated(),
        make_snapshot_created(),
        make_dashboard_refresh_requested(),
    ],
)
def test_concrete_events_have_valid_ids_and_utc_timestamps(
    event: DomainEvent,
) -> None:
    """Default metadata contains a non-nil UUID and an aware UTC timestamp."""
    assert isinstance(event.id, UUID)
    assert event.id.int != 0
    assert event.occurred_at.tzinfo is not None
    assert event.occurred_at.utcoffset() == timedelta(0)


def test_event_payload_types_are_exact() -> None:
    """Public event annotations use only the intended exact domain types."""
    assert get_type_hints(TransactionAdded) == {
        "occurred_at": datetime,
        "id": UUID,
        "transaction_id": UUID,
        "portfolio_id": UUID,
        "asset_id": UUID,
        "transaction_type": TransactionType,
        "quantity": Decimal,
        "unit_price": Decimal,
        "currency": Currency,
    }
    assert get_type_hints(PortfolioUpdated) == {
        "occurred_at": datetime,
        "id": UUID,
        "portfolio_id": UUID,
        "reason": EventReasonCode,
        "source_event_id": UUID,
    }
    assert get_type_hints(SnapshotCreated) == {
        "occurred_at": datetime,
        "id": UUID,
        "snapshot_id": UUID,
        "portfolio_id": UUID,
        "total_value": Decimal,
        "currency": Currency,
        "source_event_id": UUID,
    }
    assert get_type_hints(DashboardRefreshRequested) == {
        "occurred_at": datetime,
        "id": UUID,
        "portfolio_id": UUID,
        "reason": EventReasonCode,
        "source_event_id": UUID,
    }


def test_financial_values_retain_decimal_precision() -> None:
    """Event construction preserves exact Decimal values."""
    quantity = Decimal("0.12345678901234567890123456789")
    unit_price = Decimal("19.990000000000000000000000001")
    total_value = Decimal("2469.1357802469135780246913578")

    transaction = make_transaction_added(
        quantity=quantity,
        unit_price=unit_price,
    )
    snapshot = make_snapshot_created(total_value=total_value)

    assert transaction.quantity == quantity
    assert transaction.unit_price == unit_price
    assert snapshot.total_value == total_value


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (make_transaction_added, "quantity"),
        (make_transaction_added, "unit_price"),
        (make_snapshot_created, "total_value"),
    ],
)
def test_float_financial_values_are_rejected(
    factory: object,
    field_name: str,
) -> None:
    """Financial event payloads reject binary floating-point values."""
    event_factory = factory
    assert callable(event_factory)
    with pytest.raises(TypeError, match="must be a Decimal"):
        event_factory(**{field_name: 5 / 4})


@pytest.mark.parametrize(
    "factory",
    [
        make_transaction_added,
        make_portfolio_updated,
        make_snapshot_created,
        make_dashboard_refresh_requested,
    ],
)
def test_nil_event_id_is_rejected(factory: object) -> None:
    """Every concrete event rejects a nil event identifier."""
    event_factory = factory
    assert callable(event_factory)
    with pytest.raises(ValueError, match="id must be a non-nil UUID"):
        event_factory(id=UUID(int=0))


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (make_transaction_added, "transaction_id"),
        (make_transaction_added, "portfolio_id"),
        (make_transaction_added, "asset_id"),
        (make_portfolio_updated, "portfolio_id"),
        (make_portfolio_updated, "source_event_id"),
        (make_snapshot_created, "snapshot_id"),
        (make_snapshot_created, "portfolio_id"),
        (make_snapshot_created, "source_event_id"),
        (make_dashboard_refresh_requested, "portfolio_id"),
        (make_dashboard_refresh_requested, "source_event_id"),
    ],
)
def test_required_identifiers_are_validated(
    factory: object,
    field_name: str,
) -> None:
    """Required payload identifiers cannot be nil UUIDs."""
    event_factory = factory
    assert callable(event_factory)
    with pytest.raises(ValueError, match=f"{field_name} must be a non-nil UUID"):
        event_factory(**{field_name: UUID(int=0)})


@pytest.mark.parametrize(
    ("factory", "reason"),
    [
        (make_portfolio_updated, EventReasonCode.TRANSACTION_ADDED),
        (
            make_dashboard_refresh_requested,
            EventReasonCode.SNAPSHOT_CREATED,
        ),
    ],
)
def test_reason_uses_stable_machine_readable_code(
    factory: object,
    reason: EventReasonCode,
) -> None:
    """Reason-bearing events preserve stable locale-independent codes."""
    event_factory = factory
    assert callable(event_factory)
    event = event_factory(reason=reason)
    assert event.reason is reason


@pytest.mark.parametrize(
    "factory",
    [
        make_portfolio_updated,
        make_dashboard_refresh_requested,
    ],
)
def test_reason_rejects_free_form_text(factory: object) -> None:
    """Reason-bearing events reject strings that merely resemble reason codes."""
    event_factory = factory
    assert callable(event_factory)
    with pytest.raises(
        TypeError,
        match="reason must be an instance of EventReasonCode",
    ):
        event_factory(reason="transaction_added")


def test_source_event_relationships_are_preserved() -> None:
    """Derived events retain the exact identifier of their source event."""
    source_event_id = uuid4()

    portfolio = make_portfolio_updated(source_event_id=source_event_id)
    snapshot = make_snapshot_created(source_event_id=source_event_id)
    dashboard = make_dashboard_refresh_requested(
        source_event_id=source_event_id,
    )

    assert portfolio.source_event_id == source_event_id
    assert snapshot.source_event_id == source_event_id
    assert dashboard.source_event_id == source_event_id


@pytest.mark.parametrize(
    "occurred_at",
    [
        datetime(2026, 7, 18, 12, 0),
        datetime(2026, 7, 18, 12, 0, tzinfo=timezone(timedelta(hours=3))),
    ],
)
def test_timestamp_must_be_utc_aware(occurred_at: datetime) -> None:
    """Concrete events reject naive and non-UTC timestamps."""
    with pytest.raises(ValueError, match="UTC"):
        make_transaction_added(occurred_at=occurred_at)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("quantity", Decimal("0"), "quantity must be positive"),
        ("quantity", Decimal("-1"), "quantity must be positive"),
        ("unit_price", Decimal("-0.01"), "unit_price cannot be negative"),
        ("quantity", Decimal("NaN"), "quantity must be finite"),
    ],
)
def test_transaction_financial_invariants(
    field_name: str,
    value: Decimal,
    message: str,
) -> None:
    """Transaction events enforce finite, correctly signed amounts."""
    with pytest.raises(ValueError, match=message):
        make_transaction_added(**{field_name: value})
