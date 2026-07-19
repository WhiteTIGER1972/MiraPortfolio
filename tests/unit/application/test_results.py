"""Contract tests for immutable application result DTOs."""

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.results import AssetView
from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency


def test_asset_view_has_exact_descriptive_immutable_contract() -> None:
    asset_id = UUID("d574800a-bbbb-4aa5-9c47-0cc25b30e0b2")
    created_at = datetime(2026, 7, 20, 9, 15, 30, 123456, tzinfo=UTC)
    view = AssetView(
        id=asset_id,
        symbol="BIST",
        name="BIST Asset",
        asset_type=AssetType.EQUITY,
        currency=Currency.TRY,
        is_active=True,
        created_at=created_at,
    )

    assert tuple(field.name for field in fields(AssetView)) == (
        "id",
        "symbol",
        "name",
        "asset_type",
        "currency",
        "is_active",
        "created_at",
    )
    assert tuple(field.type for field in fields(AssetView)) == (
        UUID,
        str,
        str,
        AssetType,
        Currency,
        bool,
        datetime,
    )
    assert view.id is asset_id
    assert view.asset_type is AssetType.EQUITY
    assert view.currency is Currency.TRY
    assert view.created_at is created_at
    assert view.created_at.tzinfo is UTC
    assert not hasattr(view, "__dict__")
    assert not {
        "quantity",
        "average_cost",
        "cost_basis",
        "market_price",
        "market_value",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
        "allocation",
        "provider",
    } & {field.name for field in fields(AssetView)}
    with pytest.raises(FrozenInstanceError):
        setattr(view, "symbol", "CHANGED")
