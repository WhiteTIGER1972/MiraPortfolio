"""Framework-independent transaction boundary contract."""

from collections.abc import Sequence
from types import TracebackType
from typing import Literal, Protocol, Self
from uuid import UUID

from app.domain.entities.asset import Asset
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.price_history import PriceHistory
from app.domain.entities.snapshot import Snapshot


class _Repository[EntityT](Protocol):
    """Describe the domain repository operations used by application services."""

    def add(self, entity: EntityT) -> EntityT:
        """Add a domain entity to the current transaction."""

    def get(self, identity: UUID) -> EntityT | None:
        """Return a domain entity by identifier, or None when absent."""

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[EntityT]:
        """Return a deterministic page of domain entities."""

    def delete(self, entity: EntityT) -> None:
        """Delete a domain entity when it exists."""

    def exists(self, identity: UUID) -> bool:
        """Return whether an entity exists."""

    def count(self) -> int:
        """Return the total entity count."""


class UnitOfWork(Protocol):
    """Define explicit transaction lifecycle operations for application use cases."""

    @property
    def assets(self) -> _Repository[Asset]:
        """Return the active Asset repository."""

    @property
    def portfolios(self) -> _Repository[Portfolio]:
        """Return the active Portfolio repository."""

    @property
    def price_history(self) -> _Repository[PriceHistory]:
        """Return the active PriceHistory repository."""

    @property
    def snapshots(self) -> _Repository[Snapshot]:
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
