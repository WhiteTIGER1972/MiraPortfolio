"""Contract tests for immutable application result DTOs."""

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.application.results import (
    AssetView,
    CurrencyValuationView,
    MarketPriceView,
    PortfolioDashboard,
    PortfolioDetails,
    ValuedAssetPositionView,
)
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


def test_market_price_view_has_exact_persisted_value_contract() -> None:
    price_id = UUID("5701e5ca-1b16-4124-a459-0663e1d9624a")
    asset_id = UUID("067430e8-e8af-4608-963d-294aca6f4369")
    price = Decimal("123.456700")
    observed_at = datetime(2026, 7, 20, 10, 45, 15, 654321, tzinfo=UTC)
    view = MarketPriceView(
        id=price_id,
        asset_id=asset_id,
        price=price,
        currency=Currency.USD,
        observed_at=observed_at,
    )

    assert tuple(field.name for field in fields(MarketPriceView)) == (
        "id",
        "asset_id",
        "price",
        "currency",
        "observed_at",
    )
    assert view.id is price_id
    assert view.asset_id is asset_id
    assert view.price is price
    assert view.currency is Currency.USD
    assert view.observed_at is observed_at
    assert view.observed_at.tzinfo is UTC
    assert not hasattr(view, "__dict__")
    assert not {
        "provider",
        "source",
        "exchange",
        "return_percentage",
        "is_stale",
        "formatted_price",
    } & {field.name for field in fields(MarketPriceView)}
    with pytest.raises(FrozenInstanceError):
        setattr(view, "price", Decimal("1"))


def test_valued_asset_position_view_has_exact_typed_contract() -> None:
    asset_id = UUID("a3420e58-7d40-49a2-a690-54a90ec79130")
    quantity = Decimal("12.5")
    average_cost = Decimal("100.25")
    cost_basis = Decimal("1253.125")
    market_price = Decimal("110.75")
    price_observed_at = datetime(2026, 7, 20, 11, 30, 45, 123456, tzinfo=UTC)
    market_value = Decimal("1384.375")
    realized_pnl = Decimal("25.50")
    unrealized_pnl = Decimal("131.250")
    total_pnl = Decimal("156.750")
    view = ValuedAssetPositionView(
        asset_id=asset_id,
        symbol="EXACT",
        name="Exact Asset",
        asset_type=AssetType.ETF,
        currency=Currency.EUR,
        quantity=quantity,
        average_cost=average_cost,
        cost_basis=cost_basis,
        market_price=market_price,
        price_observed_at=price_observed_at,
        market_value=market_value,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        total_pnl=total_pnl,
    )

    assert tuple(field.name for field in fields(ValuedAssetPositionView)) == (
        "asset_id",
        "symbol",
        "name",
        "asset_type",
        "currency",
        "quantity",
        "average_cost",
        "cost_basis",
        "market_price",
        "price_observed_at",
        "market_value",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
    )
    assert view.asset_id is asset_id
    assert view.asset_type is AssetType.ETF
    assert view.currency is Currency.EUR
    assert view.quantity is quantity
    assert view.average_cost is average_cost
    assert view.cost_basis is cost_basis
    assert view.market_price is market_price
    assert view.price_observed_at is price_observed_at
    assert view.market_value is market_value
    assert view.realized_pnl is realized_pnl
    assert view.unrealized_pnl is unrealized_pnl
    assert view.total_pnl is total_pnl
    assert not hasattr(view, "__dict__")
    assert not {
        "asset",
        "position",
        "allocation",
        "return_percentage",
        "fx_rate",
        "converted_value",
        "base_currency_value",
        "daily_change",
        "provider",
        "formatted_value",
    } & {field.name for field in fields(ValuedAssetPositionView)}
    with pytest.raises(FrozenInstanceError):
        setattr(view, "quantity", Decimal("0"))


def test_valued_asset_position_view_supports_closed_position_representation() -> None:
    zero = Decimal("0")
    view = ValuedAssetPositionView(
        asset_id=UUID("267b5860-5d5f-4d0e-a8d8-a16903226431"),
        symbol="CLOSED",
        name="Closed Asset",
        asset_type=AssetType.EQUITY,
        currency=Currency.TRY,
        quantity=zero,
        average_cost=zero,
        cost_basis=zero,
        market_price=None,
        price_observed_at=None,
        market_value=zero,
        realized_pnl=Decimal("50"),
        unrealized_pnl=zero,
        total_pnl=Decimal("50"),
    )
    field_types = {field.name: field.type for field in fields(ValuedAssetPositionView)}

    assert view.market_price is None
    assert view.price_observed_at is None
    assert view.quantity is zero
    assert view.market_value is zero
    assert view.unrealized_pnl is zero
    assert view.total_pnl == view.realized_pnl
    assert field_types["market_price"] == Decimal | None
    assert field_types["price_observed_at"] == datetime | None
    for field_name in (
        "quantity",
        "average_cost",
        "cost_basis",
        "market_value",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
    ):
        assert field_types[field_name] is Decimal


def test_currency_valuation_view_has_exact_decimal_only_bucket_contract() -> None:
    view = CurrencyValuationView(
        currency=Currency.GBP,
        cost_basis=Decimal("100"),
        market_value=Decimal("125"),
        realized_pnl=Decimal("5"),
        unrealized_pnl=Decimal("25"),
        total_pnl=Decimal("30"),
    )

    assert tuple(field.name for field in fields(CurrencyValuationView)) == (
        "currency",
        "cost_basis",
        "market_value",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
    )
    assert view.currency is Currency.GBP
    assert all(
        isinstance(getattr(view, field_name), Decimal)
        for field_name in (
            "cost_basis",
            "market_value",
            "realized_pnl",
            "unrealized_pnl",
            "total_pnl",
        )
    )
    assert not hasattr(view, "__dict__")
    assert not {
        "fx_rate",
        "converted_value",
        "percentage",
        "is_grand_total",
    } & {field.name for field in fields(CurrencyValuationView)}
    with pytest.raises(FrozenInstanceError):
        setattr(view, "market_value", Decimal("0"))


def test_portfolio_dashboard_nests_details_and_supports_empty_immutable_tuples() -> None:
    created_at = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    details = PortfolioDetails(
        id=UUID("2fe4b458-d745-4ceb-b15b-c1c947c0ae23"),
        name="Empty Dashboard",
        base_currency=Currency.TRY,
        assets=(),
        transactions=(),
        is_archived=False,
        created_at=created_at,
    )
    dashboard = PortfolioDashboard(
        portfolio=details,
        positions=(),
        currencies=(),
    )

    assert tuple(field.name for field in fields(PortfolioDashboard)) == (
        "portfolio",
        "positions",
        "currencies",
    )
    assert dashboard.portfolio is details
    assert dashboard.positions == ()
    assert dashboard.currencies == ()
    assert not hasattr(dashboard, "__dict__")
    assert not {
        "transactions",
        "prices",
        "total_cost_basis",
        "total_market_value",
        "total_pnl",
        "base_currency_total",
        "fx_rate",
        "widget_state",
    } & {field.name for field in fields(PortfolioDashboard)}
    with pytest.raises(FrozenInstanceError):
        setattr(dashboard, "positions", ())
