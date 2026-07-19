"""Immutable input DTOs for portfolio application queries."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ListAssetsQuery:
    """Request a page of available assets."""

    offset: int = 0
    limit: int = 100


@dataclass(frozen=True, slots=True)
class GetPortfolioQuery:
    """Request one portfolio by identity."""

    portfolio_id: UUID


@dataclass(frozen=True, slots=True)
class ListPortfoliosQuery:
    """Request the available portfolios."""


__all__ = ["GetPortfolioQuery", "ListAssetsQuery", "ListPortfoliosQuery"]
