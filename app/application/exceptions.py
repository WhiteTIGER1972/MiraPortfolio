"""Framework-independent exceptions raised by application use cases."""


class ApplicationError(Exception):
    """Base exception for application-layer failures."""


class PortfolioNotFoundError(ApplicationError):
    """Raised when a requested portfolio does not exist."""


class AssetNotFoundError(ApplicationError):
    """Raised when a requested asset does not exist."""


class ValidationError(ApplicationError):
    """Raised when application input cannot be accepted."""


__all__ = [
    "ApplicationError",
    "AssetNotFoundError",
    "PortfolioNotFoundError",
    "ValidationError",
]
