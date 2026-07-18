"""Event raised after a transaction has been accepted by the application."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.domain.entities.transaction import TransactionType
from app.domain.events._validation import (
    validate_decimal,
    validate_enum,
    validate_utc_timestamp,
    validate_uuid,
)
from app.domain.events.base import DomainEvent
from app.domain.value_objects.currency import Currency


@dataclass(frozen=True, slots=True, kw_only=True)
class TransactionAdded(DomainEvent):
    """Describe an accepted portfolio transaction without persistence details."""

    transaction_id: UUID
    portfolio_id: UUID
    asset_id: UUID
    transaction_type: TransactionType
    quantity: Decimal
    unit_price: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        """Validate identifiers, financial values, enums, and event metadata."""
        validate_uuid(self.id, "id")
        validate_uuid(self.transaction_id, "transaction_id")
        validate_uuid(self.portfolio_id, "portfolio_id")
        validate_uuid(self.asset_id, "asset_id")
        validate_enum(self.transaction_type, TransactionType, "transaction_type")
        validate_decimal(self.quantity, "quantity", strictly_positive=True)
        validate_decimal(self.unit_price, "unit_price")
        validate_enum(self.currency, Currency, "currency")
        validate_utc_timestamp(self.occurred_at, "occurred_at")
