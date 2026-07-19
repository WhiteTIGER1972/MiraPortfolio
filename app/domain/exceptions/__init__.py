"""Domain-specific exception types."""

from uuid import UUID


class DomainError(Exception):
    """Base exception for domain rule violations."""


class DomainValidationError(DomainError):
    """Raised when a domain entity violates a business invariant."""


class TransactionNotFoundError(DomainError):
    """Raised when a portfolio does not own the requested transaction."""

    def __init__(self, transaction_id: UUID) -> None:
        self.transaction_id = transaction_id
        super().__init__(f"Transaction {transaction_id} does not belong to this portfolio.")
