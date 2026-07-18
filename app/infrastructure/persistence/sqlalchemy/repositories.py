"""Domain-facing repositories backed by one injected SQLAlchemy session."""

from collections.abc import Callable, Sequence
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import InstrumentedAttribute, Session, selectinload

from app.core.exceptions import RepositoryError
from app.domain.entities.asset import Asset
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.price_history import PriceHistory
from app.domain.entities.snapshot import Snapshot
from app.infrastructure.persistence.sqlalchemy.base import Base
from app.infrastructure.persistence.sqlalchemy.mappers import (
    asset_to_domain,
    asset_to_model,
    portfolio_to_domain,
    portfolio_to_model,
    price_history_to_domain,
    price_history_to_model,
    snapshot_to_domain,
    snapshot_to_model,
)
from app.infrastructure.persistence.sqlalchemy.models import (
    AssetModel,
    PortfolioModel,
    PriceHistoryModel,
    SnapshotModel,
)


class _ModelStore[ModelT: Base]:
    """Provide typed ORM operations without exposing models publicly."""

    def __init__(
        self,
        session: Session,
        model_type: type[ModelT],
        identity_attribute: InstrumentedAttribute[UUID],
    ) -> None:
        self._session = session
        self._model_type = model_type
        self._identity_attribute = identity_attribute

    def get(self, identity: UUID) -> ModelT | None:
        return self._session.get(self._model_type, identity)

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[ModelT]:
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive.")
        statement: Select[tuple[ModelT]] = (
            select(self._model_type).order_by(self._identity_attribute).offset(offset).limit(limit)
        )
        return self._session.scalars(statement).all()

    def add(self, model: ModelT) -> ModelT:
        self._session.add(model)
        self._flush("create")
        return model

    def delete(self, model: ModelT) -> None:
        self._session.delete(model)
        self._flush("delete")

    def exists(self, identity: UUID) -> bool:
        statement = (
            select(1)
            .select_from(self._model_type)
            .where(self._identity_attribute == identity)
            .limit(1)
        )
        return self._session.scalar(statement) is not None

    def count(self) -> int:
        statement = select(func.count()).select_from(self._model_type)
        return int(self._session.scalar(statement) or 0)

    def _flush(self, operation: str) -> None:
        try:
            self._session.flush()
        except SQLAlchemyError as error:
            raise RepositoryError(
                f"Could not {operation} {self._model_type.__name__} entity."
            ) from error


class _DomainRepository[DomainT, ModelT: Base]:
    """Adapt the existing ORM helper to return domain entities."""

    def __init__(
        self,
        session: Session,
        model_type: type[ModelT],
        identity_attribute: InstrumentedAttribute[UUID],
        to_domain: Callable[[ModelT], DomainT],
    ) -> None:
        self._session = session
        self._models = _ModelStore(session, model_type, identity_attribute)
        self._to_domain = to_domain

    def get(self, identity: UUID) -> DomainT | None:
        model = self._models.get(identity)
        return self._to_domain(model) if model is not None else None

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[DomainT]:
        return [self._to_domain(model) for model in self._models.list(offset=offset, limit=limit)]

    def exists(self, identity: UUID) -> bool:
        return self._models.exists(identity)

    def count(self) -> int:
        return self._models.count()

    def _delete_by_id(self, identity: UUID) -> None:
        model = self._models.get(identity)
        if model is not None:
            self._models.delete(model)


class SQLAlchemyAssetRepository(_DomainRepository[Asset, AssetModel]):
    """Persist and reconstruct Asset domain entities."""

    def __init__(self, session: Session) -> None:
        super().__init__(session, AssetModel, AssetModel.id, asset_to_domain)

    def add(self, entity: Asset) -> Asset:
        self._models.add(asset_to_model(entity))
        return entity

    def delete(self, entity: Asset) -> None:
        self._delete_by_id(entity.id)


class SQLAlchemyPortfolioRepository(_DomainRepository[Portfolio, PortfolioModel]):
    """Persist and reconstruct Portfolio aggregates."""

    def __init__(self, session: Session) -> None:
        super().__init__(session, PortfolioModel, PortfolioModel.id, portfolio_to_domain)
        self._assets = _ModelStore(session, AssetModel, AssetModel.id)

    @staticmethod
    def _aggregate_statement() -> Select[tuple[PortfolioModel]]:
        return select(PortfolioModel).options(
            selectinload(PortfolioModel.asset_links),
            selectinload(PortfolioModel.transactions),
        )

    def get(self, identity: UUID) -> Portfolio | None:
        model = self._session.scalar(
            self._aggregate_statement().where(PortfolioModel.id == identity)
        )
        return portfolio_to_domain(model) if model is not None else None

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Portfolio]:
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive.")
        statement = (
            self._aggregate_statement().order_by(PortfolioModel.id).offset(offset).limit(limit)
        )
        return [portfolio_to_domain(model) for model in self._session.scalars(statement)]

    def add(self, entity: Portfolio) -> Portfolio:
        for asset in entity.assets:
            persisted = self._assets.get(asset.id)
            if persisted is None:
                self._assets.add(asset_to_model(asset))
            elif asset_to_domain(persisted) != asset:
                raise RepositoryError(
                    "Portfolio references an asset identifier with conflicting values."
                )
        self._models.add(portfolio_to_model(entity))
        return entity

    def delete(self, entity: Portfolio) -> None:
        self._delete_by_id(entity.id)


class SQLAlchemyPriceHistoryRepository(_DomainRepository[PriceHistory, PriceHistoryModel]):
    """Persist and reconstruct PriceHistory domain entities."""

    def __init__(self, session: Session) -> None:
        super().__init__(
            session,
            PriceHistoryModel,
            PriceHistoryModel.id,
            price_history_to_domain,
        )

    def add(self, entity: PriceHistory) -> PriceHistory:
        self._models.add(price_history_to_model(entity))
        return entity

    def delete(self, entity: PriceHistory) -> None:
        self._delete_by_id(entity.id)


class SQLAlchemySnapshotRepository(_DomainRepository[Snapshot, SnapshotModel]):
    """Persist and reconstruct Snapshot domain entities."""

    def __init__(self, session: Session) -> None:
        super().__init__(session, SnapshotModel, SnapshotModel.id, snapshot_to_domain)

    def add(self, entity: Snapshot) -> Snapshot:
        self._models.add(snapshot_to_model(entity))
        return entity

    def delete(self, entity: Snapshot) -> None:
        self._delete_by_id(entity.id)


__all__ = [
    "SQLAlchemyAssetRepository",
    "SQLAlchemyPortfolioRepository",
    "SQLAlchemyPriceHistoryRepository",
    "SQLAlchemySnapshotRepository",
]
