"""Domain-specific exception types."""

from decimal import Decimal
from uuid import UUID

from app.domain.entities.transaction import TransactionType


class DomainError(Exception):
    """Base exception for domain rule violations."""


class DomainValidationError(DomainError):
    """Raised when a domain entity violates a business invariant."""


class TransactionNotFoundError(DomainError):
    """Raised when a portfolio does not own the requested transaction."""

    def __init__(self, transaction_id: UUID) -> None:
        self.transaction_id = transaction_id
        super().__init__(f"Transaction {transaction_id} does not belong to this portfolio.")


class InsufficientPositionError(DomainError):
    """Raised when a sale exceeds the available long position."""

    def __init__(
        self,
        asset_id: UUID,
        available_quantity: Decimal,
        requested_quantity: Decimal,
    ) -> None:
        self.asset_id = asset_id
        self.available_quantity = available_quantity
        self.requested_quantity = requested_quantity
        super().__init__(
            f"Asset {asset_id} has {available_quantity} available; "
            f"cannot sell {requested_quantity}."
        )


class UnsupportedTransactionTypeError(DomainError):
    """Raised when a position calculation encounters an unsupported transaction."""

    def __init__(
        self,
        transaction_id: UUID,
        transaction_type: TransactionType,
    ) -> None:
        self.transaction_id = transaction_id
        self.transaction_type = transaction_type
        super().__init__(
            f"Transaction {transaction_id} has unsupported type {transaction_type.value}."
        )


__all__ = [
    "DomainError",
    "DomainValidationError",
    "InsufficientPositionError",
    "TransactionNotFoundError",
    "UnsupportedTransactionTypeError",
]
