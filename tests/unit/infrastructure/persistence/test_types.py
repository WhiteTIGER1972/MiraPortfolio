"""Unit tests for exact persistence boundary value adapters."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect

from app.infrastructure.persistence.sqlalchemy.base import ExactDecimal, UTCDateTime


def test_exact_decimal_preserves_precision_exponent_and_nullable_values() -> None:
    adapter = ExactDecimal()
    dialect = sqlite_dialect()
    value = Decimal("12345678901234567890.123400")

    stored = adapter.process_bind_param(value, dialect)
    reconstructed = adapter.process_result_value(stored, dialect)

    assert stored == "12345678901234567890.123400"
    assert reconstructed is not None
    assert reconstructed.as_tuple() == value.as_tuple()
    assert adapter.process_bind_param(None, dialect) is None
    assert adapter.process_result_value(None, dialect) is None


def test_exact_decimal_rejects_float_and_non_finite_values() -> None:
    adapter = ExactDecimal()
    dialect = sqlite_dialect()
    runtime_float = cast(Decimal, 1.25)

    with pytest.raises(TypeError, match="Decimal instances"):
        adapter.process_bind_param(runtime_float, dialect)
    with pytest.raises(ValueError, match="finite"):
        adapter.process_bind_param(Decimal("NaN"), dialect)


def test_utc_datetime_round_trip_preserves_instant_and_nullable_values() -> None:
    adapter = UTCDateTime()
    dialect = sqlite_dialect()
    value = datetime(2026, 7, 18, 12, 30, 45, 123456, tzinfo=UTC)

    stored = adapter.process_bind_param(value, dialect)
    reconstructed = adapter.process_result_value(stored, dialect)

    assert stored == "2026-07-18T12:30:45.123456+00:00"
    assert reconstructed == value
    assert reconstructed is not None
    assert reconstructed.tzinfo is UTC
    assert adapter.process_bind_param(None, dialect) is None
    assert adapter.process_result_value(None, dialect) is None


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 7, 18, 12, 30),
        datetime(
            2026,
            7,
            18,
            15,
            30,
            tzinfo=timezone(timedelta(hours=3)),
        ),
    ],
)
def test_utc_datetime_rejects_naive_and_non_utc_values(value: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        UTCDateTime().process_bind_param(value, sqlite_dialect())
