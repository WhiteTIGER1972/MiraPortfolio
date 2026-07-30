"""Domain-specific exception types."""


class MiraPortfolioError(Exception):
    """Base exception for expected application errors."""


class ConfigurationError(MiraPortfolioError):
    """Raised when configuration is invalid."""


class DatabaseError(MiraPortfolioError):
    """Raised when persistence cannot complete."""


class BackupError(MiraPortfolioError):
    """Raised when a database backup operation cannot complete."""


class BackupNotSupportedError(BackupError):
    """Raised when the configured database cannot be backed up safely."""


class BackupCreationError(BackupError):
    """Raised when a backup cannot be created atomically."""


class BackupVerificationError(BackupError):
    """Raised when a backup archive is invalid or incompatible."""


class ConcurrentDatabaseChangeError(BackupCreationError):
    """Raised when the source changes while its backup is being captured."""


class RepositoryError(DatabaseError):
    """Raised when a repository cannot complete a persistence operation."""


class ProviderError(MiraPortfolioError):
    """Raised when an external data provider fails."""


class ValidationError(MiraPortfolioError):
    """Raised when a domain invariant is violated."""
