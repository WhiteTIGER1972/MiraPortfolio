"""Desktop dependency composition root."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.application.services import (
    AssetApplicationService,
    DefaultAssetApplicationService,
    DefaultMarketPriceApplicationService,
    DefaultPortfolioApplicationService,
    DefaultPortfolioDashboardQueryService,
    MarketPriceApplicationService,
    PortfolioApplicationService,
    PortfolioDashboardQueryService,
)
from app.application.unit_of_work import UnitOfWork
from app.core.settings import Settings
from app.infrastructure.database import DatabaseManager
from app.infrastructure.persistence.sqlalchemy.unit_of_work import (
    SQLAlchemyUnitOfWork,
)


@dataclass(frozen=True, slots=True)
class Container:
    """Hold desktop infrastructure and application service contracts."""

    settings: Settings
    database_manager: DatabaseManager
    session_factory: sessionmaker[Session]
    unit_of_work_factory: Callable[[], UnitOfWork]
    portfolio_application_service: PortfolioApplicationService
    asset_application_service: AssetApplicationService
    market_price_application_service: MarketPriceApplicationService
    portfolio_dashboard_query_service: PortfolioDashboardQueryService


def build_container(
    settings: Settings,
    database_manager: DatabaseManager,
) -> Container:
    """Compose application services around the initialized database manager."""
    session_factory = database_manager.session_factory

    def unit_of_work_factory() -> UnitOfWork:
        return SQLAlchemyUnitOfWork(session_factory)

    return Container(
        settings=settings,
        database_manager=database_manager,
        session_factory=session_factory,
        unit_of_work_factory=unit_of_work_factory,
        portfolio_application_service=DefaultPortfolioApplicationService(
            unit_of_work_factory,
        ),
        asset_application_service=DefaultAssetApplicationService(
            unit_of_work_factory,
        ),
        market_price_application_service=DefaultMarketPriceApplicationService(
            unit_of_work_factory,
        ),
        portfolio_dashboard_query_service=DefaultPortfolioDashboardQueryService(
            unit_of_work_factory,
        ),
    )


__all__ = ["Container", "build_container"]
