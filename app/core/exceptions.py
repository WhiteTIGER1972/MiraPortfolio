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


class RestoreError(MiraPortfolioError):
    """Raised when a restart-safe restore operation cannot complete."""


class RestoreNotSupportedError(RestoreError):
    """Raised when the configured database cannot be restored safely."""


class RestoreAlreadyPendingError(RestoreError):
    """Raised when staging would replace an existing pending restore."""


class RestoreStagingError(RestoreError):
    """Raised when a backup cannot be staged without affecting active data."""


class RestoreVerificationError(RestoreError):
    """Raised when restore metadata or a staged database is invalid."""


class RestoreApplicationError(RestoreError):
    """Raised when startup cannot install a staged restore."""


class RestoreRecoveryError(RestoreApplicationError):
    """Raised when interrupted-operation recovery or rollback cannot finish."""


class PendingRestoreCorruptError(RestoreVerificationError):
    """Raised when pending restore state is malformed or unsafe."""


class DiagnosticsError(MiraPortfolioError):
    """Raised when privacy-safe diagnostics cannot complete."""


class DiagnosticsCollectionError(DiagnosticsError):
    """Raised when required diagnostic metadata cannot be collected safely."""


class SupportBundleCreationError(DiagnosticsError):
    """Raised when a support bundle cannot be created atomically."""


class SupportBundleVerificationError(DiagnosticsError):
    """Raised when a support bundle is invalid or violates its privacy contract."""


class RepositoryError(DatabaseError):
    """Raised when a repository cannot complete a persistence operation."""


class ProviderError(MiraPortfolioError):
    """Raised when an external data provider fails."""


class ValidationError(MiraPortfolioError):
    """Raised when a domain invariant is violated."""
