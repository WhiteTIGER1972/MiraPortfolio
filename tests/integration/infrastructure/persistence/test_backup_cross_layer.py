"""Cross-layer test from Container services through a verified backup payload."""

from __future__ import annotations

import shutil
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.application.commands import (
    BuyAssetCommand,
    CreateAssetCommand,
    CreatePortfolioCommand,
    RecordMarketPriceCommand,
)
from app.core.container import build_container
from app.core.settings import Settings
from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency
from app.infrastructure.database import DatabaseManager
from app.infrastructure.persistence.database_backup import DATABASE_MEMBER
from app.infrastructure.persistence.sqlalchemy.session import (
    create_persistence_engine,
    create_persistence_session_factory,
)
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SQLAlchemyUnitOfWork


def test_container_backup_round_trip_is_readable_through_real_repositories(
    backup_settings: Settings,
    tmp_path: Path,
) -> None:
    manager = DatabaseManager(backup_settings).initialize()
    container = build_container(backup_settings, manager)
    occurred_at = datetime(2026, 7, 30, 18, 0, tzinfo=UTC)
    try:
        asset = container.asset_application_service.create_asset(
            CreateAssetCommand(
                symbol="CROSS",
                name="Cross-layer Asset",
                asset_type=AssetType.EQUITY,
                currency=Currency.TRY,
            )
        )
        portfolio = container.portfolio_application_service.create_portfolio(
            CreatePortfolioCommand(portfolio_name="Cross-layer Portfolio")
        )
        transaction = container.portfolio_application_service.buy_asset(
            BuyAssetCommand(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                quantity=Decimal("3.25"),
                unit_price=Decimal("101.50"),
                commission=Decimal("1.00"),
                tax=Decimal("0"),
                trade_datetime=occurred_at,
            )
        )
        market_price = container.market_price_application_service.record_market_price(
            RecordMarketPriceCommand(
                asset_id=asset.id,
                price=Decimal("110.75"),
                observed_at=occurred_at,
            )
        )

        record = container.backup_service.create_backup()
        assert container.backup_service.verify_backup(record.path) == record
        assert manager.health_check()

        extracted = tmp_path / "cross-layer.sqlite"
        with zipfile.ZipFile(record.path) as archive:
            with archive.open(DATABASE_MEMBER) as source:
                with extracted.open("xb") as destination:
                    shutil.copyfileobj(source, destination)

        engine = create_persistence_engine(f"sqlite:///{extracted.as_posix()}")
        try:
            factory = create_persistence_session_factory(engine)
            with SQLAlchemyUnitOfWork(factory) as unit_of_work:
                restored_asset = unit_of_work.assets.get(asset.id)
                restored_portfolio = unit_of_work.portfolios.get(portfolio.id)
                restored_price = unit_of_work.price_history.get_latest_for_asset(asset.id)

                assert restored_asset is not None
                assert restored_asset.symbol == "CROSS"
                assert restored_portfolio is not None
                assert restored_portfolio.name == "Cross-layer Portfolio"
                assert tuple(item.id for item in restored_portfolio.transactions) == (
                    transaction.id,
                )
                assert restored_portfolio.transactions[0].quantity == Decimal("3.25")
                assert restored_price is not None
                assert restored_price.id == market_price.id
                assert restored_price.price == Decimal("110.75")
        finally:
            engine.dispose()
    finally:
        manager.shutdown()
