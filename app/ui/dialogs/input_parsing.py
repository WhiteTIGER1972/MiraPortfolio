"""Exact input parsing shared by transaction and price dialogs."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from uuid import UUID

from PySide6.QtCore import QDateTime

from app.domain.value_objects.currency import Currency

_ZERO = Decimal("0")


class AssetOption(Protocol):
    """Read-only Asset fields required by UI selection controls."""

    @property
    def id(self) -> UUID: ...

    @property
    def symbol(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def currency(self) -> Currency: ...


def parse_decimal(
    text: str,
    *,
    field_name: str,
    strictly_positive: bool = False,
) -> Decimal:
    """Parse one finite Decimal and enforce the requested lower bound."""
    stripped = text.strip()
    if not stripped:
        raise ValueError(f"{field_name} is required.")
    try:
        value = Decimal(stripped)
    except InvalidOperation as error:
        raise ValueError(f"{field_name} must be a valid decimal number.") from error
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite.")
    if strictly_positive and value <= _ZERO:
        raise ValueError(f"{field_name} must be greater than zero.")
    if not strictly_positive and value < _ZERO:
        raise ValueError(f"{field_name} cannot be negative.")
    return value


def qdatetime_to_utc(value: QDateTime) -> datetime:
    """Convert a Qt datetime to aware UTC using integer milliseconds."""
    seconds, remaining_milliseconds = divmod(value.toMSecsSinceEpoch(), 1000)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=remaining_milliseconds * 1000)


def uuid_from_item_data(value: object, *, field_name: str = "Asset") -> UUID:
    """Return a UUID stored as safe Qt item data."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} selection is invalid.")
    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError(f"{field_name} selection is invalid.") from error


__all__ = ["AssetOption", "parse_decimal", "qdatetime_to_utc", "uuid_from_item_data"]
