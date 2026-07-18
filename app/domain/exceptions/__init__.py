"""Domain-specific exception types."""


class DomainError(Exception):
    """Base exception for domain rule violations."""


class DomainValidationError(DomainError):
    """Raised when a domain entity violates a business invariant."""
