"""Restart-style restore through real Container services and repositories."""

from __future__ import annotations

import shutil
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.application.commands import (
    BuyAssetCommand,
    CreateAssetCommand,
    CreatePortfolioCommand,
    RecordMarketPriceCommand,
)
from app.application.queries import (
    GetLatestMarketPriceQuery,
    GetPortfolioQuery,
    ListAssetsQuery,
    ListPortfoliosQuery,
)
from app.application.restore import RestoreOutcome
from app.core.container import Container, build_container
from app.core.exceptions import DatabaseError
from app.core.settings import Settings
from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency
from app.infrastructure.database import DatabaseManager
from app.infrastructure.persistence.database_backup import DATABASE_MEMBER
from app.infrastructure.persistence.database_preparation import prepare_database
from app.infrastructure.persistence.database_restore import StartupRestoreCoordinator

OCCURRED_AT = datetime(2026, 7, 30, 21, 0, tzinfo=UTC)


def make_current_settings(tmp_path: Path, name: str) -> Settings:
    root = tmp_path / name
    database_directory = root / "database"
    database_directory.mkdir(parents=True)
    database = database_directory / "portfolio.db"
    settings = Settings(
        _env_file=None,
        data_directory=root,
        database_directory=database_directory,
        database_path=database,
        database_url=f"sqlite:///{database.as_posix()}",
        backup_directory=root / "backups",
    )
    prepare_database(settings, legacy_search_directory=root)
    return settings


def populate(container: Container, label: str) -> tuple[object, object]:
    asset = container.asset_application_service.create_asset(
        CreateAssetCommand(
            symbol=label,
            name=f"{label} Asset",
            asset_type=AssetType.EQUITY,
            currency=Currency.TRY,
        )
    )
    portfolio = container.portfolio_application_service.create_portfolio(
        CreatePortfolioCommand(portfolio_name=f"{label} Portfolio")
    )
    container.portfolio_application_service.buy_asset(
        BuyAssetCommand(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            quantity=Decimal("4.5"),
            unit_price=Decimal("100.25"),
            commission=Decimal("1.00"),
            tax=Decimal("0"),
            trade_datetime=OCCURRED_AT,
        )
    )
    container.market_price_application_service.record_market_price(
        RecordMarketPriceCommand(
            asset_id=asset.id,
            price=Decimal("111.75"),
            observed_at=OCCURRED_AT,
        )
    )
    return asset, portfolio


def test_restart_style_restore_replaces_data_and_preserves_old_data_in_safety_backup(
    tmp_path: Path,
) -> None:
    source_settings = make_current_settings(tmp_path, "restore-source")
    source_manager = DatabaseManager(source_settings).initialize()
    try:
        source_container = build_container(source_settings, source_manager)
        source_asset, source_portfolio = populate(source_container, "SOURCE")
        source_backup = source_container.backup_service.create_backup()
    finally:
        source_manager.shutdown()

    active_settings = make_current_settings(tmp_path, "restore-active")
    first_manager = DatabaseManager(active_settings).initialize()
    try:
        first_container = build_container(active_settings, first_manager)
        old_asset, old_portfolio = populate(first_container, "OLD")
        staged = first_container.restore_service.stage_restore(source_backup.path)
        assert staged.restart_required
        assert first_manager.health_check()
        assert {
            item.symbol
            for item in first_container.asset_application_service.list_assets(ListAssetsQuery())
        } == {"OLD"}
    finally:
        first_manager.shutdown()

    with pytest.raises(DatabaseError, match="not been initialized"):
        _ = first_manager.engine
    applied = StartupRestoreCoordinator(active_settings).apply_pending_restore()
    assert applied.outcome is RestoreOutcome.APPLIED
    assert applied.pre_restore_backup is not None

    second_manager = DatabaseManager(active_settings).initialize()
    try:
        second_container = build_container(active_settings, second_manager)
        assert {
            item.symbol
            for item in second_container.asset_application_service.list_assets(ListAssetsQuery())
        } == {"SOURCE"}
        portfolios = second_container.portfolio_application_service.list_portfolios(
            ListPortfoliosQuery()
        )
        assert tuple(item.id for item in portfolios) == (source_portfolio.id,)
        details = second_container.portfolio_application_service.get_portfolio(
            GetPortfolioQuery(portfolio_id=source_portfolio.id)
        )
        assert len(details.transactions) == 1
        assert details.transactions[0].asset_id == source_asset.id
        latest = second_container.market_price_application_service.get_latest_market_price(
            GetLatestMarketPriceQuery(asset_id=source_asset.id)
        )
        assert latest is not None
        assert latest.price == Decimal("111.75")
    finally:
        second_manager.shutdown()

    extracted = tmp_path / "pre-restore-old.sqlite"
    with zipfile.ZipFile(applied.pre_restore_backup.path) as archive:
        with archive.open(DATABASE_MEMBER) as source:
            with extracted.open("xb") as destination:
                shutil.copyfileobj(source, destination)
    old_settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{extracted.as_posix()}",
        database_path=extracted,
        database_directory=tmp_path,
        backup_directory=tmp_path / "unused-backups",
    )
    old_manager = DatabaseManager(old_settings).initialize()
    try:
        old_container = build_container(old_settings, old_manager)
        assert {
            item.symbol
            for item in old_container.asset_application_service.list_assets(ListAssetsQuery())
        } == {"OLD"}
        old_details = old_container.portfolio_application_service.get_portfolio(
            GetPortfolioQuery(portfolio_id=old_portfolio.id)
        )
        assert len(old_details.transactions) == 1
        old_latest = old_container.market_price_application_service.get_latest_market_price(
            GetLatestMarketPriceQuery(asset_id=old_asset.id)
        )
        assert old_latest is not None
        assert old_latest.price == Decimal("111.75")
    finally:
        old_manager.shutdown()

    assert not (active_settings.database_directory / "restore").exists()
