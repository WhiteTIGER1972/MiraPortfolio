"""Integration tests for SQLAlchemy portfolio aggregate updates."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.application.ports import (
    AssetRepository,
    PortfolioRepository,
    PriceHistoryRepository,
    SnapshotRepository,
)
from app.core.exceptions import RepositoryError
from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.snapshot import Snapshot
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.value_objects.currency import Currency
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SQLAlchemyUnitOfWork

NOW = datetime(2026, 7, 19, 18, 45, 12, 654321, tzinfo=UTC)


def make_asset(*, identity: int, symbol: str) -> Asset:
    return Asset(
        id=UUID(int=identity),
        symbol=symbol,
        name=f"{symbol} Asset",
        asset_type=AssetType.EQUITY,
        currency=Currency.TRY,
        created_at=NOW,
    )


def make_transaction(
    *,
    identity: int,
    asset_id: UUID,
    quantity: str,
    price: str,
    transaction_type: TransactionType = TransactionType.BUY,
) -> Transaction:
    return Transaction(
        id=UUID(int=identity),
        asset_id=asset_id,
        quantity=Decimal(quantity),
        price=Decimal(price),
        transaction_type=transaction_type,
        commission=Decimal("1.2300"),
        tax=Decimal("0.4500"),
        date=NOW,
    )


def make_portfolio(
    *,
    identity: int,
    asset: Asset,
    transaction_identity: int,
) -> Portfolio:
    portfolio = Portfolio(
        id=UUID(int=identity),
        name=f"Portfolio {identity}",
        base_currency=Currency.TRY,
        created_at=NOW,
    )
    portfolio.add_asset(asset)
    portfolio.record_transaction(
        make_transaction(
            identity=transaction_identity,
            asset_id=asset.id,
            quantity="10.2500",
            price="123456789.000000000123400",
        )
    )
    return portfolio


def persist_portfolios(
    session_factory: sessionmaker[Session],
    *portfolios: Portfolio,
) -> None:
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        for portfolio in portfolios:
            unit_of_work.portfolios.add(portfolio)
        unit_of_work.commit()


def test_unit_of_work_repositories_satisfy_explicit_application_ports(
    session_factory: sessionmaker[Session],
) -> None:
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        assets: AssetRepository = unit_of_work.assets
        portfolios: PortfolioRepository = unit_of_work.portfolios
        price_history: PriceHistoryRepository = unit_of_work.price_history
        snapshots: SnapshotRepository = unit_of_work.snapshots

        assert assets is unit_of_work.assets
        assert portfolios is unit_of_work.portfolios
        assert price_history is unit_of_work.price_history
        assert snapshots is unit_of_work.snapshots


def test_asset_delete_uses_uuid_is_noop_when_missing_and_never_commits(
    session_factory: sessionmaker[Session],
) -> None:
    first = make_asset(identity=1, symbol="FIRST")
    second = make_asset(identity=2, symbol="SECOND")
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.assets.add(first)
        unit_of_work.assets.add(second)
        unit_of_work.commit()

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.assets.delete(first.id)
        unit_of_work.assets.delete(UUID(int=999))

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        assert reader.assets.get(first.id) == first
        assert reader.assets.get(second.id) == second

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.assets.delete(first.id)
        unit_of_work.commit()

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        assert reader.assets.get(first.id) is None
        assert reader.assets.get(second.id) == second


def test_portfolio_delete_uses_uuid_and_preserves_existing_cascade_policy(
    session_factory: sessionmaker[Session],
) -> None:
    first_asset = make_asset(identity=1, symbol="FIRST")
    second_asset = make_asset(identity=2, symbol="SECOND")
    first = make_portfolio(identity=11, asset=first_asset, transaction_identity=101)
    second = make_portfolio(identity=12, asset=second_asset, transaction_identity=102)
    snapshot = Snapshot(
        id=UUID(int=201),
        portfolio_id=first.id,
        total_value=Decimal("100.00"),
        currency=Currency.TRY,
        captured_at=NOW,
    )
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.portfolios.add(first)
        unit_of_work.portfolios.add(second)
        unit_of_work.snapshots.add(snapshot)
        unit_of_work.commit()

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.portfolios.delete(first.id)
        unit_of_work.portfolios.delete(UUID(int=999))

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        assert reader.portfolios.get(first.id) == first
        assert reader.portfolios.get(second.id) == second

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.portfolios.delete(first.id)
        unit_of_work.commit()

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        assert reader.portfolios.get(first.id) is None
        assert reader.portfolios.get(second.id) == second
        assert reader.snapshots.get(snapshot.id) is None
        assert reader.assets.get(first_asset.id) == first_asset


def test_portfolio_save_returns_domain_and_persists_supported_scalars(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=1, symbol="SCALAR")
    portfolio = make_portfolio(identity=21, asset=asset, transaction_identity=201)
    persist_portfolios(session_factory, portfolio)

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        loaded = unit_of_work.portfolios.get(portfolio.id)
        assert loaded is not None
        loaded.name = "Updated Portfolio"
        loaded.base_currency = Currency.USD
        loaded.is_archived = True

        saved = unit_of_work.portfolios.save(loaded)

        assert saved is loaded
        assert isinstance(saved, Portfolio)
        assert saved.id == portfolio.id
        unit_of_work.commit()

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        reloaded = reader.portfolios.get(portfolio.id)
        assert reloaded is not None
        assert reloaded.name == "Updated Portfolio"
        assert reloaded.base_currency is Currency.USD
        assert reloaded.is_archived
        assert reloaded.id == portfolio.id


def test_portfolio_save_reconciles_new_transaction_and_asset_association(
    session_factory: sessionmaker[Session],
) -> None:
    first_asset = make_asset(identity=1, symbol="FIRST")
    second_asset = make_asset(identity=2, symbol="SECOND")
    portfolio = make_portfolio(identity=31, asset=first_asset, transaction_identity=301)
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.portfolios.add(portfolio)
        unit_of_work.assets.add(second_asset)
        unit_of_work.commit()
    new_transaction = make_transaction(
        identity=302,
        asset_id=second_asset.id,
        quantity="2.000000000123400",
        price="0.0000000000000009876500",
        transaction_type=TransactionType.SELL,
    )

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        loaded = unit_of_work.portfolios.get(portfolio.id)
        persisted_second_asset = unit_of_work.assets.get(second_asset.id)
        assert loaded is not None
        assert persisted_second_asset is not None
        loaded.add_asset(persisted_second_asset)
        loaded.record_transaction(new_transaction)

        saved = unit_of_work.portfolios.save(loaded)

        assert saved is loaded
        unit_of_work.commit()

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        reloaded = reader.portfolios.get(portfolio.id)
        assert reloaded is not None
        assert [asset.id for asset in reloaded.assets] == [
            first_asset.id,
            second_asset.id,
        ]
        assert [transaction.id for transaction in reloaded.transactions] == [
            UUID(int=301),
            new_transaction.id,
        ]
        persisted_transaction = reloaded.transactions[1]
        assert persisted_transaction.asset_id == second_asset.id
        assert persisted_transaction.quantity == Decimal("2.000000000123400")
        assert persisted_transaction.price == Decimal("0.0000000000000009876500")
        assert persisted_transaction.date == NOW
        assert persisted_transaction.date.tzinfo is UTC


def test_portfolio_save_persists_transaction_removal_and_remaining_order(
    session_factory: sessionmaker[Session],
) -> None:
    first_asset = make_asset(identity=1, symbol="FIRST")
    second_asset = make_asset(identity=2, symbol="SECOND")
    portfolio = make_portfolio(identity=41, asset=first_asset, transaction_identity=401)
    portfolio.add_asset(second_asset)
    middle = make_transaction(
        identity=402,
        asset_id=second_asset.id,
        quantity="2",
        price="20",
    )
    last = make_transaction(
        identity=403,
        asset_id=first_asset.id,
        quantity="3",
        price="30",
    )
    portfolio.record_transaction(middle)
    portfolio.record_transaction(last)
    persist_portfolios(session_factory, portfolio)

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        loaded = unit_of_work.portfolios.get(portfolio.id)
        assert loaded is not None
        loaded.remove_transaction(middle.id)
        unit_of_work.portfolios.save(loaded)
        unit_of_work.commit()

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        reloaded = reader.portfolios.get(portfolio.id)
        assert reloaded is not None
        assert [transaction.id for transaction in reloaded.transactions] == [
            UUID(int=401),
            last.id,
        ]
        assert [asset.id for asset in reloaded.assets] == [
            first_asset.id,
            second_asset.id,
        ]
        assert reader.assets.get(first_asset.id) == first_asset
        assert reader.assets.get(second_asset.id) == second_asset


def test_portfolio_save_rejects_asset_association_removal(
    session_factory: sessionmaker[Session],
) -> None:
    first_asset = make_asset(identity=1, symbol="FIRST")
    second_asset = make_asset(identity=2, symbol="SECOND")
    portfolio = make_portfolio(identity=45, asset=first_asset, transaction_identity=451)
    portfolio.add_asset(second_asset)
    persist_portfolios(session_factory, portfolio)

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        loaded = unit_of_work.portfolios.get(portfolio.id)
        assert loaded is not None
        loaded.assets.remove(second_asset)
        with pytest.raises(RepositoryError, match="cannot be removed"):
            unit_of_work.portfolios.save(loaded)

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        reloaded = reader.portfolios.get(portfolio.id)
        assert reloaded is not None
        assert [asset.id for asset in reloaded.assets] == [
            first_asset.id,
            second_asset.id,
        ]
        assert reader.assets.get(second_asset.id) == second_asset


def test_repeated_portfolio_save_does_not_duplicate_rows(
    session_factory: sessionmaker[Session],
) -> None:
    first_asset = make_asset(identity=1, symbol="FIRST")
    second_asset = make_asset(identity=2, symbol="SECOND")
    portfolio = make_portfolio(identity=51, asset=first_asset, transaction_identity=501)
    portfolio.add_asset(second_asset)
    portfolio.record_transaction(
        make_transaction(
            identity=502,
            asset_id=second_asset.id,
            quantity="5",
            price="50",
        )
    )
    persist_portfolios(session_factory, portfolio)

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        loaded = unit_of_work.portfolios.get(portfolio.id)
        assert loaded is not None
        assert unit_of_work.portfolios.save(loaded) is loaded
        assert unit_of_work.portfolios.save(loaded) is loaded
        unit_of_work.commit()

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        assert reader.portfolios.count() == 1
        assert reader.assets.count() == 2
        assert reader._session is not None
        counts = reader._session.execute(
            text(
                "SELECT "
                "(SELECT COUNT(*) FROM portfolio_assets), "
                "(SELECT COUNT(*) FROM transactions)"
            )
        ).one()
        assert counts == (2, 2)


def test_portfolio_save_is_rolled_back_by_unit_of_work(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=1, symbol="ROLLBACK")
    portfolio = make_portfolio(identity=61, asset=asset, transaction_identity=601)
    persist_portfolios(session_factory, portfolio)
    new_transaction = make_transaction(
        identity=602,
        asset_id=asset.id,
        quantity="6",
        price="60",
    )

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        loaded = unit_of_work.portfolios.get(portfolio.id)
        assert loaded is not None
        loaded.record_transaction(new_transaction)
        unit_of_work.portfolios.save(loaded)
        unit_of_work.rollback()

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        reloaded = reader.portfolios.get(portfolio.id)
        assert reloaded is not None
        assert [transaction.id for transaction in reloaded.transactions] == [UUID(int=601)]


def test_portfolio_save_rejects_missing_target_without_upsert(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=1, symbol="MISSING")
    missing = make_portfolio(identity=71, asset=asset, transaction_identity=701)
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.assets.add(asset)
        unit_of_work.commit()

    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        with pytest.raises(RepositoryError, match="does not exist"):
            unit_of_work.portfolios.save(missing)
        assert unit_of_work.portfolios.count() == 0
