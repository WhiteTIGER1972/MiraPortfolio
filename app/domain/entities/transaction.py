"""Persistence-agnostic portfolio transaction domain entity."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class TransactionType(StrEnum):
    """Represent financial trades and portfolio corporate actions."""

    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    RIGHTS_ISSUE = "rights_issue"
    BONUS_ISSUE = "bonus_issue"
    STOCK_SPLIT = "stock_split"

    @property
    def is_corporate_action(self) -> bool:
        """Return whether this transaction represents a corporate action."""
        return self in {
            TransactionType.DIVIDEND,
            TransactionType.RIGHTS_ISSUE,
            TransactionType.BONUS_ISSUE,
            TransactionType.STOCK_SPLIT,
        }


@dataclass(slots=True)
class Transaction:
    """Represent a financial event independently of any storage technology.

    The quantity and price fields have action-specific meaning. For dividends,
    ``quantity * price`` is the gross distribution. For bonus issues and stock
    splits, ``price`` must be zero because the event does not purchase shares.

    Args:
        asset_id: Identifier of the asset affected by this transaction.
        quantity: Number of units transacted or affected by the action.
        price: Unit price, subscription price, or per-unit dividend.
        transaction_type: Trade or corporate action classification.
        commission: Brokerage commission charged for the transaction.
        tax: Tax charged for the transaction.
        date: Timestamp at which the transaction occurred.
    """

    asset_id: UUID
    quantity: Decimal
    price: Decimal
    transaction_type: TransactionType = TransactionType.BUY
    commission: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    date: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        """Validate a newly created transaction and normalize its timestamp."""
        self.validate()
        if self.date.tzinfo is None:
            self.date = self.date.replace(tzinfo=UTC)

    def calculate_total(self) -> Decimal:
        """Calculate the gross value plus commission and tax."""
        return (self.quantity * self.price) + self.commission + self.tax

    def validate(self) -> None:
        """Ensure financial values satisfy transaction-type invariants.

        Raises:
            ValueError: If the transaction's values do not match its type.
        """
        if self.quantity <= 0:
            raise ValueError("Transaction quantity must be positive.")
        if self.price < 0:
            raise ValueError("Transaction price cannot be negative.")
        if self.commission < 0:
            raise ValueError("Transaction commission cannot be negative.")
        if self.tax < 0:
            raise ValueError("Transaction tax cannot be negative.")

        zero_price_actions = {
            TransactionType.BONUS_ISSUE,
            TransactionType.STOCK_SPLIT,
        }
        if self.transaction_type in zero_price_actions and self.price != Decimal("0"):
            raise ValueError("Bonus issues and stock splits must use a zero transaction price.")
        if self.transaction_type is TransactionType.RIGHTS_ISSUE and self.price <= 0:
            raise ValueError("Rights issues require a positive subscription price.")
