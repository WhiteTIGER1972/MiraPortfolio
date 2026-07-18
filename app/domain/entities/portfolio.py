"""Portfolio domain aggregate."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.entities.asset import Asset
from app.domain.entities.transaction import Transaction
from app.domain.value_objects.currency import Currency


@dataclass(slots=True)
class Portfolio:
    """Represent an investment portfolio and the positions it owns.

    Portfolio is responsible only for its assets and transaction history.
    Analytical values such as ROI, allocation, and risk belong to
    ``PortfolioMetrics``.
    """

    name: str
    base_currency: Currency = Currency.TRY
    id: UUID = field(default_factory=uuid4)
    assets: list[Asset] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    is_archived: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        """Validate and normalize the portfolio name."""
        if not self.name.strip():
            raise ValueError("Portfolio name cannot be empty.")
        self.name = self.name.strip()

    def add_asset(self, asset: Asset) -> None:
        """Add an asset unless the portfolio already contains it."""
        if any(existing_asset.id == asset.id for existing_asset in self.assets):
            raise ValueError("Portfolio already contains this asset.")
        self.assets.append(asset)

    def record_transaction(self, transaction: Transaction) -> None:
        """Record a transaction for an asset held by the portfolio."""
        if not any(asset.id == transaction.asset_id for asset in self.assets):
            raise ValueError("Transaction asset must belong to the portfolio.")
        self.transactions.append(transaction)
