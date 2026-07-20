"""Contract tests for immutable application query DTOs."""

from dataclasses import FrozenInstanceError, fields
from uuid import UUID

import pytest

from app.application.queries import (
    GetLatestMarketPriceQuery,
    GetPortfolioDashboardQuery,
    ListAssetsQuery,
)

ASSET_ID = UUID("960190ca-f47e-413b-8428-bd81861d960b")
PORTFOLIO_ID = UUID("b35e162c-81d3-488c-98fc-66edbaa1c422")


def test_list_assets_query_has_repository_pagination_defaults() -> None:
    query = ListAssetsQuery()

    assert tuple(field.name for field in fields(ListAssetsQuery)) == ("offset", "limit")
    assert query.offset == 0
    assert query.limit == 100
    assert not hasattr(query, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(query, "offset", 10)


def test_list_assets_query_preserves_supplied_values_without_validation() -> None:
    query = ListAssetsQuery(offset=-1, limit=0)

    assert query.offset == -1
    assert query.limit == 0


def test_get_latest_market_price_query_has_exact_uuid_contract() -> None:
    query = GetLatestMarketPriceQuery(asset_id=ASSET_ID)

    assert tuple(field.name for field in fields(GetLatestMarketPriceQuery)) == ("asset_id",)
    assert query.asset_id is ASSET_ID
    assert not hasattr(query, "__dict__")
    assert not {
        "symbol",
        "portfolio_id",
        "as_of",
        "provider",
        "currency",
        "is_stale",
    } & {field.name for field in fields(GetLatestMarketPriceQuery)}
    with pytest.raises(FrozenInstanceError):
        setattr(query, "asset_id", PORTFOLIO_ID)


def test_get_portfolio_dashboard_query_has_exact_uuid_contract() -> None:
    query = GetPortfolioDashboardQuery(portfolio_id=PORTFOLIO_ID)

    assert tuple(field.name for field in fields(GetPortfolioDashboardQuery)) == ("portfolio_id",)
    assert query.portfolio_id is PORTFOLIO_ID
    assert not hasattr(query, "__dict__")
    assert not {
        "currency",
        "base_currency",
        "date_from",
        "date_to",
        "provider",
        "allow_partial",
        "refresh",
        "ui_state",
    } & {field.name for field in fields(GetPortfolioDashboardQuery)}
    with pytest.raises(FrozenInstanceError):
        setattr(query, "portfolio_id", ASSET_ID)
