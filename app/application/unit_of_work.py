"""Framework-independent transaction boundary contract."""

from types import TracebackType
from typing import Literal, Protocol, Self

from app.application.ports import (
    AssetRepository,
    PortfolioRepository,
    PriceHistoryRepository,
    SnapshotRepository,
)


class UnitOfWork(Protocol):
    """Define explicit transaction lifecycle operations for application use cases."""

    @property
    def assets(self) -> AssetRepository:
        """Return the active Asset repository."""

    @property
    def portfolios(self) -> PortfolioRepository:
        """Return the active Portfolio repository."""

    @property
    def price_history(self) -> PriceHistoryRepository:
        """Return the active PriceHistory repository."""

    @property
    def snapshots(self) -> SnapshotRepository:
        """Return the active Snapshot repository."""

    def __enter__(self) -> Self:
        """Open one transaction boundary."""

    def commit(self) -> None:
        """Persist the current transaction explicitly."""

    def rollback(self) -> None:
        """Discard the current transaction explicitly."""

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        """Close the boundary without suppressing exceptions."""


__all__ = ["UnitOfWork"]
