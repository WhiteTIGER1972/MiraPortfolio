"""Framework-independent repository ports used by application services."""

from abc import abstractmethod
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.entities.asset import Asset
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.price_history import PriceHistory
from app.domain.entities.snapshot import Snapshot


class AssetRepository(Protocol):
    """Persist and retrieve assets by their domain-owned identity."""

    @abstractmethod
    def add(self, asset: Asset) -> Asset:
        """Introduce a new asset."""

    @abstractmethod
    def get(self, asset_id: UUID) -> Asset | None:
        """Return an asset by identity, or None when absent."""

    @abstractmethod
    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Asset]:
        """Return a deterministic page of assets."""

    @abstractmethod
    def delete(self, asset_id: UUID) -> None:
        """Delete an asset by identity when it exists."""

    @abstractmethod
    def exists(self, asset_id: UUID) -> bool:
        """Return whether an asset identity exists."""

    @abstractmethod
    def count(self) -> int:
        """Return the total asset count."""


class PortfolioRepository(Protocol):
    """Persist and retrieve portfolio aggregates."""

    @abstractmethod
    def add(self, portfolio: Portfolio) -> Portfolio:
        """Introduce a new portfolio aggregate."""

    @abstractmethod
    def save(self, portfolio: Portfolio) -> Portfolio:
        """Persist the current state of an existing portfolio aggregate."""

    @abstractmethod
    def get(self, portfolio_id: UUID) -> Portfolio | None:
        """Return a portfolio by identity, or None when absent."""

    @abstractmethod
    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Portfolio]:
        """Return a deterministic page of portfolios."""

    @abstractmethod
    def delete(self, portfolio_id: UUID) -> None:
        """Delete a portfolio by identity when it exists."""

    @abstractmethod
    def exists(self, portfolio_id: UUID) -> bool:
        """Return whether a portfolio identity exists."""

    @abstractmethod
    def count(self) -> int:
        """Return the total portfolio count."""


class PriceHistoryRepository(Protocol):
    """Persist and retrieve historical asset prices."""

    @abstractmethod
    def add(self, price_history: PriceHistory) -> PriceHistory:
        """Introduce a historical asset price."""

    @abstractmethod
    def get(self, price_history_id: UUID) -> PriceHistory | None:
        """Return a historical price by identity, or None when absent."""

    @abstractmethod
    def get_latest_for_asset(self, asset_id: UUID) -> PriceHistory | None:
        """Return the latest price for an Asset, or None when absent."""

    @abstractmethod
    def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> Sequence[PriceHistory]:
        """Return a deterministic page of historical prices."""

    @abstractmethod
    def delete(self, price_history: PriceHistory) -> None:
        """Delete a historical price when it exists."""

    @abstractmethod
    def exists(self, price_history_id: UUID) -> bool:
        """Return whether a historical price identity exists."""

    @abstractmethod
    def count(self) -> int:
        """Return the total historical price count."""


class SnapshotRepository(Protocol):
    """Persist and retrieve portfolio snapshots."""

    @abstractmethod
    def add(self, snapshot: Snapshot) -> Snapshot:
        """Introduce a portfolio snapshot."""

    @abstractmethod
    def get(self, snapshot_id: UUID) -> Snapshot | None:
        """Return a snapshot by identity, or None when absent."""

    @abstractmethod
    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Snapshot]:
        """Return a deterministic page of snapshots."""

    @abstractmethod
    def delete(self, snapshot: Snapshot) -> None:
        """Delete a snapshot when it exists."""

    @abstractmethod
    def exists(self, snapshot_id: UUID) -> bool:
        """Return whether a snapshot identity exists."""

    @abstractmethod
    def count(self) -> int:
        """Return the total snapshot count."""


__all__ = [
    "AssetRepository",
    "PortfolioRepository",
    "PriceHistoryRepository",
    "SnapshotRepository",
]
