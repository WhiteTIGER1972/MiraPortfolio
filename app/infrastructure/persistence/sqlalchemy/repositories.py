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
    PortfolioAssetModel,
    PortfolioModel,
    PriceHistoryModel,
    SnapshotModel,
    TransactionModel,
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

    def delete(self, asset_id: UUID) -> None:
        self._delete_by_id(asset_id)


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

    def _get_model(self, portfolio_id: UUID) -> PortfolioModel | None:
        return self._session.scalar(
            self._aggregate_statement().where(PortfolioModel.id == portfolio_id)
        )

    def get(self, identity: UUID) -> Portfolio | None:
        model = self._get_model(identity)
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

    def save(self, portfolio: Portfolio) -> Portfolio:
        desired = portfolio_to_model(portfolio)
        persisted = self._get_model(desired.id)
        if persisted is None:
            raise RepositoryError(f"Portfolio {desired.id} does not exist.")

        self._validate_assets(portfolio, persisted)
        self._validate_unique_child_identities(desired)
        self._stage_owned_positions(persisted, desired)

        persisted.name = desired.name
        persisted.base_currency = desired.base_currency
        persisted.is_archived = desired.is_archived
        persisted.created_at = desired.created_at
        self._reconcile_asset_links(persisted, desired)
        self._reconcile_transactions(persisted, desired)
        self._flush("update")
        return portfolio

    def delete(self, portfolio_id: UUID) -> None:
        self._delete_by_id(portfolio_id)

    def _validate_assets(
        self,
        portfolio: Portfolio,
        persisted: PortfolioModel,
    ) -> None:
        desired_asset_ids = {asset.id for asset in portfolio.assets}
        persisted_asset_ids = {link.asset_id for link in persisted.asset_links}
        if not persisted_asset_ids.issubset(desired_asset_ids):
            raise RepositoryError("Portfolio asset associations cannot be removed through save.")
        for asset in portfolio.assets:
            persisted_asset = self._assets.get(asset.id)
            if persisted_asset is None:
                raise RepositoryError(f"Portfolio references Asset {asset.id} that does not exist.")
            if asset_to_domain(persisted_asset) != asset:
                raise RepositoryError(
                    "Portfolio references an asset identifier with conflicting values."
                )

    @staticmethod
    def _validate_unique_child_identities(portfolio: PortfolioModel) -> None:
        asset_ids = [link.asset_id for link in portfolio.asset_links]
        if len(asset_ids) != len(set(asset_ids)):
            raise RepositoryError("Portfolio contains duplicate asset identifiers.")
        transaction_ids = [transaction.id for transaction in portfolio.transactions]
        if len(transaction_ids) != len(set(transaction_ids)):
            raise RepositoryError("Portfolio contains duplicate transaction identifiers.")

    def _stage_owned_positions(
        self,
        persisted: PortfolioModel,
        desired: PortfolioModel,
    ) -> None:
        asset_start = (
            max(
                max((link.position for link in persisted.asset_links), default=-1),
                len(desired.asset_links) - 1,
            )
            + 1
        )
        transaction_start = (
            max(
                max(
                    (transaction.position for transaction in persisted.transactions),
                    default=-1,
                ),
                len(desired.transactions) - 1,
            )
            + 1
        )
        for offset, link in enumerate(persisted.asset_links):
            link.position = asset_start + offset
        for offset, transaction in enumerate(persisted.transactions):
            transaction.position = transaction_start + offset
        if persisted.asset_links or persisted.transactions:
            self._flush("stage update for")

    @staticmethod
    def _reconcile_asset_links(
        persisted: PortfolioModel,
        desired: PortfolioModel,
    ) -> None:
        existing = {link.asset_id: link for link in persisted.asset_links}
        reconciled: list[PortfolioAssetModel] = []
        for desired_link in desired.asset_links:
            link = existing.get(desired_link.asset_id)
            if link is None:
                link = PortfolioAssetModel(
                    portfolio_id=persisted.id,
                    asset_id=desired_link.asset_id,
                    position=desired_link.position,
                )
            else:
                link.position = desired_link.position
            reconciled.append(link)
        persisted.asset_links[:] = reconciled

    @staticmethod
    def _reconcile_transactions(
        persisted: PortfolioModel,
        desired: PortfolioModel,
    ) -> None:
        existing = {transaction.id: transaction for transaction in persisted.transactions}
        reconciled: list[TransactionModel] = []
        for desired_transaction in desired.transactions:
            transaction = existing.get(desired_transaction.id)
            if transaction is None:
                transaction = TransactionModel(
                    id=desired_transaction.id,
                    portfolio_id=persisted.id,
                    asset_id=desired_transaction.asset_id,
                    position=desired_transaction.position,
                    quantity=desired_transaction.quantity,
                    price=desired_transaction.price,
                    transaction_type=desired_transaction.transaction_type,
                    commission=desired_transaction.commission,
                    tax=desired_transaction.tax,
                    date=desired_transaction.date,
                )
            else:
                transaction.asset_id = desired_transaction.asset_id
                transaction.position = desired_transaction.position
                transaction.quantity = desired_transaction.quantity
                transaction.price = desired_transaction.price
                transaction.transaction_type = desired_transaction.transaction_type
                transaction.commission = desired_transaction.commission
                transaction.tax = desired_transaction.tax
                transaction.date = desired_transaction.date
            reconciled.append(transaction)
        persisted.transactions[:] = reconciled

    def _flush(self, operation: str) -> None:
        try:
            self._session.flush()
        except SQLAlchemyError as error:
            raise RepositoryError(f"Could not {operation} PortfolioModel entity.") from error


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

    def get_latest_for_asset(self, asset_id: UUID) -> PriceHistory | None:
        statement: Select[tuple[PriceHistoryModel]] = (
            select(PriceHistoryModel)
            .where(PriceHistoryModel.asset_id == asset_id)
            .order_by(
                PriceHistoryModel.observed_at.desc(),
                PriceHistoryModel.id.desc(),
            )
            .limit(1)
        )
        model = self._session.scalar(statement)
        return price_history_to_domain(model) if model is not None else None

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
