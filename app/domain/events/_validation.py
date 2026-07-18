"""Validation helpers shared by concrete domain events."""

from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from uuid import UUID


def validate_uuid(value: UUID, field_name: str) -> None:
    """Require a non-nil UUID identifier."""
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{field_name} must be a non-nil UUID.")


def validate_utc_timestamp(value: datetime, field_name: str) -> None:
    """Require a timezone-aware timestamp with a zero UTC offset."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC.")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC.")


def validate_decimal(
    value: Decimal,
    field_name: str,
    *,
    strictly_positive: bool = False,
) -> None:
    """Require an exact finite Decimal with an allowed sign."""
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal.")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite.")
    if strictly_positive and value <= Decimal("0"):
        raise ValueError(f"{field_name} must be positive.")
    if not strictly_positive and value < Decimal("0"):
        raise ValueError(f"{field_name} cannot be negative.")


def validate_enum(value: Enum, expected_type: type[Enum], field_name: str) -> None:
    """Require an instance of the expected serializable domain enum."""
    if not isinstance(value, expected_type):
        raise TypeError(f"{field_name} must be an instance of {expected_type.__name__}.")
