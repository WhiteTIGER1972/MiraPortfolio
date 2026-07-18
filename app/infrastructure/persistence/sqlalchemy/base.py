"""Shared SQLAlchemy metadata and exact persistence value types."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    """Base class and shared metadata registry for persistence models."""


class ExactDecimal(TypeDecorator[Decimal]):
    """Store finite Decimal values as text without binary floating conversion."""

    impl = String
    cache_ok = True

    def __init__(self, length: int = 100) -> None:
        super().__init__(length=length)

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        """Serialize one exact finite Decimal value."""
        del dialect
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError("ExactDecimal values must be Decimal instances.")
        if not value.is_finite():
            raise ValueError("ExactDecimal values must be finite.")
        return str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        """Reconstruct the exact Decimal, including its stored exponent."""
        del dialect
        return Decimal(value) if value is not None else None


class UTCDateTime(TypeDecorator[datetime]):
    """Store aware UTC datetimes as canonical ISO 8601 text."""

    impl = String
    cache_ok = True

    def __init__(self, length: int = 40) -> None:
        super().__init__(length=length)

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        """Require and serialize an aware UTC datetime."""
        del dialect
        if value is None:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("UTCDateTime values must be timezone-aware UTC.")
        if value.utcoffset() != timedelta(0):
            raise ValueError("UTCDateTime values must use UTC.")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        """Reconstruct an aware datetime normalized to UTC."""
        del dialect
        if value is None:
            return None
        reconstructed = datetime.fromisoformat(value)
        if reconstructed.tzinfo is None:
            raise ValueError("Stored UTCDateTime value is missing timezone information.")
        return reconstructed.astimezone(UTC)


__all__ = ["Base", "ExactDecimal", "UTCDateTime"]
