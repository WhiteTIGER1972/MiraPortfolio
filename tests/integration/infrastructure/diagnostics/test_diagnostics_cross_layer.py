"""Cross-layer diagnostics bundle verification through the application Container."""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from loguru import logger

from app.application.commands import (
    BuyAssetCommand,
    CreateAssetCommand,
    CreatePortfolioCommand,
    RecordMarketPriceCommand,
)
from app.application.queries import (
    GetPortfolioDashboardQuery,
    ListAssetsQuery,
    ListPortfoliosQuery,
)
from app.core import config, runtime_paths
from app.core.container import build_container
from app.core.logging import configure_logging
from app.core.settings import Settings
from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency
from app.infrastructure.database import DatabaseManager
from app.infrastructure.diagnostics import verify_support_bundle
from app.infrastructure.persistence.database_preparation import prepare_database


def test_container_creates_verified_bundle_without_affecting_application_data(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cross-layer"
    data = root / "data"
    database_directory = data / "database"
    database_path = database_directory / "portfolio.db"
    settings = Settings(
        _env_file=None,
        data_directory=data,
        cache_directory=root / "cache",
        database_directory=database_directory,
        export_directory=data / "exports",
        backup_directory=data / "backups",
        log_directory=root / "logs",
        database_path=database_path,
        database_url=runtime_paths.sqlite_url_for_path(database_path),
    )
    for directory in (
        settings.data_directory,
        settings.cache_directory,
        settings.database_directory,
        settings.export_directory,
        settings.backup_directory,
        settings.log_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    prepare_database(settings, legacy_search_directory=root)
    manager = DatabaseManager(settings).initialize()
    configure_logging(settings)
    try:
        container = build_container(settings, manager)
        asset = container.asset_application_service.create_asset(
            CreateAssetCommand(
                symbol="PRIVATE",
                name="Private Diagnostic Asset",
                asset_type=AssetType.EQUITY,
                currency=Currency.TRY,
            )
        )
        portfolio = container.portfolio_application_service.create_portfolio(
            CreatePortfolioCommand(portfolio_name="Private Diagnostic Portfolio")
        )
        traded_at = datetime(2026, 7, 30, 12, tzinfo=UTC)
        container.portfolio_application_service.buy_asset(
            BuyAssetCommand(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                quantity=Decimal("2"),
                unit_price=Decimal("100"),
                trade_datetime=traded_at,
            )
        )
        container.market_price_application_service.record_market_price(
            RecordMarketPriceCommand(
                asset_id=asset.id,
                price=Decimal("110"),
                observed_at=traded_at,
            )
        )
        logger.info("CROSS_LAYER_DIAGNOSTICS_READY")

        record = container.diagnostics_service.create_support_bundle()
        verified = verify_support_bundle(settings, record.path)
        with zipfile.ZipFile(record.path) as archive:
            names = set(archive.namelist())
            combined = b"\n".join(archive.read(name) for name in names)

        assert verified == record
        assert {
            "manifest.json",
            "application.json",
            "system.json",
            "database.json",
            "runtime.json",
        }.issubset(names)
        assert not any(name.endswith((".db", ".sqlite", ".mirabackup")) for name in names)
        assert b"Private Diagnostic Asset" not in combined
        assert b"Private Diagnostic Portfolio" not in combined
        assert str(tmp_path).encode() not in combined
        assert manager.health_check()
        assert container.asset_application_service.list_assets(ListAssetsQuery()) == (asset,)
        listed_portfolios = container.portfolio_application_service.list_portfolios(
            ListPortfoliosQuery()
        )
        assert tuple(item.id for item in listed_portfolios) == (portfolio.id,)
        dashboard = container.portfolio_dashboard_query_service.get_dashboard(
            GetPortfolioDashboardQuery(portfolio_id=portfolio.id)
        )
        assert dashboard.portfolio.id == portfolio.id
        support = settings.export_directory / config.SUPPORT_DIRECTORY_NAME
        assert not any(path.name.startswith(".mira-support-") for path in support.iterdir())
    finally:
        logger.remove()
        manager.shutdown()
