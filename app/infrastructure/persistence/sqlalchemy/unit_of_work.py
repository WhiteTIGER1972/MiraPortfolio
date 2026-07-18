"""SQLAlchemy Unit of Work with explicit transaction ownership."""

from types import TracebackType
from typing import Literal, Self

from sqlalchemy.orm import Session, sessionmaker

from app.application.unit_of_work import UnitOfWork
from app.infrastructure.persistence.sqlalchemy.repositories import (
    SQLAlchemyAssetRepository,
    SQLAlchemyPortfolioRepository,
    SQLAlchemyPriceHistoryRepository,
    SQLAlchemySnapshotRepository,
)


class SQLAlchemyUnitOfWork(UnitOfWork):
    """Scope repositories and an explicit transaction to one injected session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._assets: SQLAlchemyAssetRepository | None = None
        self._portfolios: SQLAlchemyPortfolioRepository | None = None
        self._price_history: SQLAlchemyPriceHistoryRepository | None = None
        self._snapshots: SQLAlchemySnapshotRepository | None = None

    @property
    def assets(self) -> SQLAlchemyAssetRepository:
        return self._require_repository(self._assets)

    @property
    def portfolios(self) -> SQLAlchemyPortfolioRepository:
        return self._require_repository(self._portfolios)

    @property
    def price_history(self) -> SQLAlchemyPriceHistoryRepository:
        return self._require_repository(self._price_history)

    @property
    def snapshots(self) -> SQLAlchemySnapshotRepository:
        return self._require_repository(self._snapshots)

    def __enter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Unit of Work is already active.")
        session = self._session_factory()
        self._session = session
        self._assets = SQLAlchemyAssetRepository(session)
        self._portfolios = SQLAlchemyPortfolioRepository(session)
        self._price_history = SQLAlchemyPriceHistoryRepository(session)
        self._snapshots = SQLAlchemySnapshotRepository(session)
        return self

    def commit(self) -> None:
        self._require_session().commit()

    def rollback(self) -> None:
        self._require_session().rollback()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exception_type, exception, traceback
        session = self._require_session()
        try:
            if session.in_transaction():
                session.rollback()
        finally:
            session.close()
            self._session = None
            self._assets = None
            self._portfolios = None
            self._price_history = None
            self._snapshots = None
        return False

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of Work is not active.")
        return self._session

    @staticmethod
    def _require_repository[RepositoryT](
        repository: RepositoryT | None,
    ) -> RepositoryT:
        if repository is None:
            raise RuntimeError("Unit of Work is not active.")
        return repository


__all__ = ["SQLAlchemyUnitOfWork"]
