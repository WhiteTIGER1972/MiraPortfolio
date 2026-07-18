"""Portfolio analytical metrics domain entity."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.value_objects.financial import Money, Percentage, RiskScore


@dataclass(slots=True)
class PortfolioMetrics:
    """Represent a point-in-time analytical view of one portfolio.

    This entity deliberately contains no assets or transaction history. Those
    are owned by ``Portfolio``; this class only keeps derived calculations.

    Args:
        portfolio_id: Identifier of the measured portfolio.
        roi: Return on investment percentage.
        profit: Realized and unrealized profit in the portfolio base currency.
        irr: Internal rate of return percentage.
        allocation: Per-asset allocation percentages keyed by asset identifier.
        risk: Aggregate portfolio risk score.
    """

    portfolio_id: UUID
    roi: Percentage
    profit: Money
    irr: Percentage
    risk: RiskScore
    allocation: dict[UUID, Percentage] = field(default_factory=dict)
    calculated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        """Validate the allocation composition and normalize its timestamp."""
        if self.calculated_at.tzinfo is None:
            self.calculated_at = self.calculated_at.replace(tzinfo=UTC)
        total_allocation = sum(
            (percentage.value for percentage in self.allocation.values()),
            start=Decimal("0"),
        )
        if total_allocation > Decimal("100"):
            raise ValueError("Portfolio allocation cannot exceed 100 percent.")

    @property
    def is_profitable(self) -> bool:
        """Return whether the portfolio has a positive profit."""
        return self.profit.amount > Decimal("0")
