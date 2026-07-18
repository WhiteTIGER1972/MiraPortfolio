"""Domain-specific exception types."""


class MiraPortfolioError(Exception):
    """Base exception for expected application errors."""


class ConfigurationError(MiraPortfolioError):
    """Raised when configuration is invalid."""


class DatabaseError(MiraPortfolioError):
    """Raised when persistence cannot complete."""


class RepositoryError(DatabaseError):
    """Raised when a repository cannot complete a persistence operation."""


class ProviderError(MiraPortfolioError):
    """Raised when an external data provider fails."""


class ValidationError(MiraPortfolioError):
    """Raised when a domain invariant is violated."""
