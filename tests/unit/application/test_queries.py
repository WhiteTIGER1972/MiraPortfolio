"""Contract tests for immutable application query DTOs."""

from dataclasses import FrozenInstanceError, fields

import pytest

from app.application.queries import ListAssetsQuery


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
