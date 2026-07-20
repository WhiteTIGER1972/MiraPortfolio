"""Tests for the desktop composition root."""

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import get_type_hints
from unittest.mock import Mock

import pytest
from sqlalchemy import event

from app.application.commands import (
    CreateAssetCommand,
    CreatePortfolioCommand,
    RecordMarketPriceCommand,
)
from app.application.queries import (
    GetPortfolioDashboardQuery,
    ListAssetsQuery,
)
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
from app.core.container import Container, build_container
from app.core.exceptions import DatabaseError
from app.core.settings import Settings
from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency
from app.infrastructure.database import DatabaseManager
from app.infrastructure.persistence.sqlalchemy.unit_of_work import (
    SQLAlchemyUnitOfWork,
)

OBSERVED_AT = datetime(2026, 7, 19, 12, 34, 56, 789012, tzinfo=UTC)


def make_settings(tmp_path: Path, name: str = "composition") -> Settings:
    """Build settings whose filesystem effects remain under one test directory."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return Settings(
        database_url=f"sqlite:///{(root / 'portfolio.db').as_posix()}",
        cache_directory=root / "cache",
        database_directory=root / "data",
        export_directory=root / "exports",
        backup_directory=root / "backups",
    )


@contextmanager
def initialized_manager(
    tmp_path: Path,
) -> Iterator[tuple[Settings, DatabaseManager]]:
    """Yield an initialized manager backed only by an isolated temporary database."""
    settings = make_settings(tmp_path)
    manager = DatabaseManager(settings).initialize()
    try:
        yield settings, manager
    finally:
        manager.shutdown()


def test_container_has_exact_immutable_typed_fields() -> None:
    assert tuple(field.name for field in fields(Container)) == (
        "settings",
        "database_manager",
        "session_factory",
        "unit_of_work_factory",
        "portfolio_application_service",
        "asset_application_service",
        "market_price_application_service",
        "portfolio_dashboard_query_service",
    )
    annotations = get_type_hints(Container)
    assert annotations["unit_of_work_factory"] == Callable[[], UnitOfWork]
    assert annotations["portfolio_application_service"] is PortfolioApplicationService
    assert annotations["asset_application_service"] is AssetApplicationService
    assert annotations["market_price_application_service"] is MarketPriceApplicationService
    assert annotations["portfolio_dashboard_query_service"] is PortfolioDashboardQueryService
    assert "__dict__" not in Container.__slots__
    assert not hasattr(Container, "resolve")
    assert not hasattr(Container, "get_service")
    assert not any("repository" in field.name for field in fields(Container))
    assert not any(field.name == "session" for field in fields(Container))


def test_build_container_preserves_supplied_lifecycle_dependencies(
    tmp_path: Path,
) -> None:
    with initialized_manager(tmp_path) as (settings, manager):
        container = build_container(settings, manager)

        assert isinstance(container, Container)
        assert container.settings is settings
        assert container.database_manager is manager
        assert container.session_factory is manager.session_factory


def test_build_container_constructs_services_behind_abstract_contracts(
    tmp_path: Path,
) -> None:
    with initialized_manager(tmp_path) as (settings, manager):
        container = build_container(settings, manager)

        assert isinstance(container.portfolio_application_service, PortfolioApplicationService)
        assert isinstance(container.asset_application_service, AssetApplicationService)
        assert isinstance(
            container.market_price_application_service,
            MarketPriceApplicationService,
        )
        assert isinstance(
            container.portfolio_dashboard_query_service,
            PortfolioDashboardQueryService,
        )
        assert type(container.portfolio_application_service) is DefaultPortfolioApplicationService
        assert type(container.asset_application_service) is DefaultAssetApplicationService
        assert (
            type(container.market_price_application_service) is DefaultMarketPriceApplicationService
        )
        assert (
            type(container.portfolio_dashboard_query_service)
            is DefaultPortfolioDashboardQueryService
        )


def test_all_services_share_the_container_unit_of_work_factory(
    tmp_path: Path,
) -> None:
    with initialized_manager(tmp_path) as (settings, manager):
        container = build_container(settings, manager)

        for service in (
            container.portfolio_application_service,
            container.asset_application_service,
            container.market_price_application_service,
            container.portfolio_dashboard_query_service,
        ):
            assert getattr(service, "_unit_of_work_factory") is container.unit_of_work_factory


def test_unit_of_work_factory_returns_fresh_inactive_sqlalchemy_units(
    tmp_path: Path,
) -> None:
    with initialized_manager(tmp_path) as (settings, manager):
        container = build_container(settings, manager)

        first = container.unit_of_work_factory()
        second = container.unit_of_work_factory()

        assert isinstance(first, SQLAlchemyUnitOfWork)
        assert isinstance(second, SQLAlchemyUnitOfWork)
        assert first is not second
        assert getattr(first, "_session") is None
        assert getattr(second, "_session") is None


def test_build_container_creates_no_session_or_database_activity(
    tmp_path: Path,
) -> None:
    with initialized_manager(tmp_path) as (settings, manager):
        statements: list[str] = []

        def record_statement(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(manager.engine, "before_cursor_execute", record_statement)
        real_session_factory = manager.session_factory
        tracked_session_factory = Mock(wraps=real_session_factory)
        setattr(manager, "_session_factory", tracked_session_factory)
        try:
            container = build_container(settings, manager)

            tracked_session_factory.assert_not_called()
            assert statements == []
            _ = container.unit_of_work_factory()
            tracked_session_factory.assert_not_called()
            assert statements == []
        finally:
            setattr(manager, "_session_factory", real_session_factory)
            event.remove(manager.engine, "before_cursor_execute", record_statement)


def test_build_container_requires_an_initialized_database_manager(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, "uninitialized")
    manager = DatabaseManager(settings)

    with pytest.raises(DatabaseError, match="has not been initialized"):
        build_container(settings, manager)

    with pytest.raises(DatabaseError, match="has not been initialized"):
        _ = manager.session_factory


def test_container_is_frozen(
    tmp_path: Path,
) -> None:
    with initialized_manager(tmp_path) as (settings, manager):
        container = build_container(settings, manager)

        for field_name, replacement in (
            ("settings", settings.model_copy()),
            ("unit_of_work_factory", lambda: None),
            ("asset_application_service", object()),
        ):
            with pytest.raises(FrozenInstanceError):
                setattr(container, field_name, replacement)


def test_independent_containers_do_not_share_factories_services_or_data(
    tmp_path: Path,
) -> None:
    first_settings = make_settings(tmp_path, "first")
    second_settings = make_settings(tmp_path, "second")
    first_manager = DatabaseManager(first_settings).initialize()
    second_manager = DatabaseManager(second_settings).initialize()
    try:
        first = build_container(first_settings, first_manager)
        second = build_container(second_settings, second_manager)

        assert first is not second
        assert first.unit_of_work_factory is not second.unit_of_work_factory
        assert first.asset_application_service is not second.asset_application_service
        first.asset_application_service.create_asset(
            CreateAssetCommand(
                symbol="ONE",
                name="First Database Asset",
                asset_type=AssetType.EQUITY,
                currency=Currency.USD,
            )
        )
        assert len(first.asset_application_service.list_assets(ListAssetsQuery())) == 1
        assert second.asset_application_service.list_assets(ListAssetsQuery()) == ()
    finally:
        first_manager.shutdown()
        second_manager.shutdown()


def test_built_container_runs_real_services_through_isolated_sqlalchemy(
    tmp_path: Path,
) -> None:
    with initialized_manager(tmp_path) as (settings, manager):
        container = build_container(settings, manager)

        asset = container.asset_application_service.create_asset(
            CreateAssetCommand(
                symbol="alpha",
                name=" Alpha Incorporated ",
                asset_type=AssetType.EQUITY,
                currency=Currency.USD,
            )
        )
        portfolio = container.portfolio_application_service.create_portfolio(
            CreatePortfolioCommand(portfolio_name="Desktop Alpha")
        )
        price = container.market_price_application_service.record_market_price(
            RecordMarketPriceCommand(
                asset_id=asset.id,
                price=Decimal("123.450000000001"),
                observed_at=OBSERVED_AT,
            )
        )
        dashboard = container.portfolio_dashboard_query_service.get_dashboard(
            GetPortfolioDashboardQuery(portfolio_id=portfolio.id)
        )

        assert container.asset_application_service.list_assets(ListAssetsQuery()) == (asset,)
        assert asset.symbol == "ALPHA"
        assert price.asset_id == asset.id
        assert price.price == Decimal("123.450000000001")
        assert price.observed_at == OBSERVED_AT
        assert dashboard.portfolio.id == portfolio.id
        assert dashboard.positions == ()
        assert dashboard.currencies == ()


def test_container_source_constructs_services_without_executing_workflows() -> None:
    source = inspect.getsource(build_container)

    assert "SQLAlchemyUnitOfWork(session_factory)" in source
    for forbidden_call in (
        ".create_asset(",
        ".list_assets(",
        ".create_portfolio(",
        ".buy_asset(",
        ".sell_asset(",
        ".record_market_price(",
        ".get_latest_market_price(",
        ".get_dashboard(",
        ".commit(",
        ".rollback(",
        "Session(",
        ".resolve(",
        ".get_service(",
    ):
        assert forbidden_call not in source
