"""Backward-compatible Portfolio export."""

from app.domain.entities.portfolio import Portfolio
from app.domain.entities.portfolio_metrics import PortfolioMetrics

__all__ = ["Portfolio", "PortfolioMetrics"]
