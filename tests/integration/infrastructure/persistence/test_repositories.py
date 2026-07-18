"""Integration tests for domain-facing SQLAlchemy repositories."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import RepositoryError
from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.price_history import PriceHistory
from app.domain.entities.snapshot import Snapshot
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.value_objects.currency import Currency
from app.infrastructure.persistence.sqlalchemy.repositories import (
    SQLAlchemyAssetRepository,
    SQLAlchemyPortfolioRepository,
    SQLAlchemyPriceHistoryRepository,
    SQLAlchemySnapshotRepository,
)

NOW = datetime(2026, 7, 18, 15, 45, 12, 654321, tzinfo=UTC)


def make_asset(*, symbol: str = "REPO", identity: UUID | None = None) -> Asset:
    return Asset(
        symbol=symbol,
        name=f"{symbol} Asset",
        asset_type=AssetType.EQUITY,
        currency=Currency.TRY,
        id=identity or uuid4(),
        created_at=NOW,
    )


def make_portfolio(asset: Asset) -> Portfolio:
    portfolio = Portfolio(
        name="Repository Portfolio",
        base_currency=Currency.TRY,
        id=uuid4(),
        created_at=NOW,
    )
    portfolio.add_asset(asset)
    portfolio.record_transaction(
        Transaction(
            asset_id=asset.id,
            quantity=Decimal("100.2500"),
            price=Decimal("123456789.000000000123400"),
            transaction_type=TransactionType.BUY,
            commission=Decimal("1.2300"),
            tax=Decimal("0.4500"),
            date=NOW,
            id=uuid4(),
        )
    )
    return portfolio


def test_asset_repository_add_get_list_count_and_exists(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    repository = SQLAlchemyAssetRepository(session)
    first = make_asset(symbol="ONE", identity=UUID(int=1))
    second = make_asset(symbol="TWO", identity=UUID(int=2))
    try:
        assert repository.add(second) is second
        repository.add(first)

        retrieved = repository.get(first.id)

        assert isinstance(retrieved, Asset)
        assert retrieved == first
        assert [asset.id for asset in repository.list()] == [first.id, second.id]
        assert repository.exists(first.id)
        assert repository.count() == 2
        repository.delete(make_asset(symbol="ABSENT", identity=UUID(int=999)))
        assert repository.count() == 2
    finally:
        session.rollback()
        session.close()


def test_asset_repository_missing_identifier_returns_none(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    try:
        assert SQLAlchemyAssetRepository(session).get(uuid4()) is None
    finally:
        session.close()


def test_repository_does_not_commit_automatically(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    session = session_factory()
    SQLAlchemyAssetRepository(session).add(asset)
    session.close()

    reader = session_factory()
    try:
        assert SQLAlchemyAssetRepository(reader).get(asset.id) is None
    finally:
        reader.close()


def test_repository_data_is_durable_after_owner_commits(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    writer = session_factory()
    SQLAlchemyAssetRepository(writer).add(asset)
    writer.commit()
    writer.close()

    reader = session_factory()
    try:
        assert SQLAlchemyAssetRepository(reader).get(asset.id) == asset
    finally:
        reader.close()


def test_duplicate_identifier_uses_existing_repository_error(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    duplicate = make_asset()
    duplicate.id = asset.id
    session = session_factory()
    repository = SQLAlchemyAssetRepository(session)
    try:
        repository.add(asset)
        with pytest.raises(RepositoryError) as raised:
            repository.add(duplicate)
        assert raised.value.__cause__ is not None
    finally:
        session.rollback()
        session.close()


def test_asset_repository_delete_removes_existing_entity(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    session = session_factory()
    repository = SQLAlchemyAssetRepository(session)
    try:
        repository.add(asset)
        repository.delete(asset)
        assert repository.get(asset.id) is None
    finally:
        session.rollback()
        session.close()


def test_portfolio_repository_round_trip_preserves_aggregate_order_and_values(
    session_factory: sessionmaker[Session],
) -> None:
    first_asset = make_asset(symbol="FIRST")
    second_asset = make_asset(symbol="SECOND")
    portfolio = make_portfolio(first_asset)
    portfolio.add_asset(second_asset)
    portfolio.record_transaction(
        Transaction(
            asset_id=second_asset.id,
            quantity=Decimal("2.0000"),
            price=Decimal("0.0000000000000009876500"),
            date=NOW,
            id=uuid4(),
        )
    )
    writer = session_factory()
    writer_repository = SQLAlchemyPortfolioRepository(writer)
    writer_repository.add(portfolio)
    assert writer_repository.get(uuid4()) is None
    assert writer_repository.list() == [portfolio]
    assert writer_repository.exists(portfolio.id)
    assert writer_repository.count() == 1
    writer_repository.delete(Portfolio(name="Absent Portfolio", id=uuid4(), created_at=NOW))
    assert writer_repository.count() == 1
    writer.commit()
    writer.close()

    reader = session_factory()
    statements: list[str] = []

    def record_statement(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    bind = reader.get_bind()
    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        reconstructed = SQLAlchemyPortfolioRepository(reader).get(portfolio.id)
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)
    try:
        assert isinstance(reconstructed, Portfolio)
        assert reconstructed == portfolio
        assert len(statements) == 3
        assert [asset.id for asset in reconstructed.assets] == [
            first_asset.id,
            second_asset.id,
        ]
        assert [transaction.id for transaction in reconstructed.transactions] == [
            transaction.id for transaction in portfolio.transactions
        ]
        assert str(reconstructed.transactions[0].price) == "123456789.000000000123400"
        assert reconstructed.transactions[0].date.tzinfo is UTC
    finally:
        reader.close()


def test_portfolio_repository_reuses_equal_existing_asset(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    session = session_factory()
    try:
        SQLAlchemyAssetRepository(session).add(asset)
        portfolio = make_portfolio(asset)
        SQLAlchemyPortfolioRepository(session).add(portfolio)
        session.commit()
    finally:
        session.close()

    reader = session_factory()
    try:
        assert SQLAlchemyPortfolioRepository(reader).get(portfolio.id) == portfolio
    finally:
        reader.close()


def test_portfolio_repository_rejects_conflicting_existing_asset(
    session_factory: sessionmaker[Session],
) -> None:
    persisted_asset = make_asset()
    conflicting_asset = make_asset(symbol="CONFLICT")
    conflicting_asset.id = persisted_asset.id
    portfolio = make_portfolio(conflicting_asset)
    session = session_factory()
    try:
        SQLAlchemyAssetRepository(session).add(persisted_asset)
        with pytest.raises(RepositoryError, match="conflicting values"):
            SQLAlchemyPortfolioRepository(session).add(portfolio)
    finally:
        session.rollback()
        session.close()


def test_price_history_repository_round_trip_uses_text_decimal_storage(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    history = PriceHistory(
        asset_id=asset.id,
        price=Decimal("999999999999999999.123400"),
        currency=Currency.USD,
        observed_at=NOW,
        id=uuid4(),
    )
    writer = session_factory()
    SQLAlchemyAssetRepository(writer).add(asset)
    SQLAlchemyPriceHistoryRepository(writer).add(history)
    writer.commit()
    raw_value = writer.execute(
        text("SELECT price, typeof(price) FROM price_history WHERE id = :id"),
        {"id": history.id.hex},
    ).one()
    writer.close()

    reader = session_factory()
    try:
        reconstructed = SQLAlchemyPriceHistoryRepository(reader).get(history.id)
        assert reconstructed == history
        assert str(reconstructed.price) == "999999999999999999.123400"
        assert reconstructed.observed_at.tzinfo is UTC
        assert raw_value == ("999999999999999999.123400", "text")
    finally:
        reader.close()


def test_snapshot_repository_round_trip_preserves_exact_decimal_and_utc(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    portfolio = make_portfolio(asset)
    snapshot = Snapshot(
        portfolio_id=portfolio.id,
        total_value=Decimal("12345678901234567890.123400"),
        currency=Currency.TRY,
        captured_at=NOW,
        id=uuid4(),
    )
    writer = session_factory()
    SQLAlchemyPortfolioRepository(writer).add(portfolio)
    SQLAlchemySnapshotRepository(writer).add(snapshot)
    writer.commit()
    writer.close()

    reader = session_factory()
    try:
        reconstructed = SQLAlchemySnapshotRepository(reader).get(snapshot.id)
        assert reconstructed == snapshot
        assert str(reconstructed.total_value) == "12345678901234567890.123400"
        assert reconstructed.captured_at.tzinfo is UTC
    finally:
        reader.close()


def test_value_repositories_support_deterministic_queries_and_deletion(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    portfolio = make_portfolio(asset)
    histories = [
        PriceHistory(
            asset_id=asset.id,
            price=Decimal("10.1000"),
            currency=Currency.TRY,
            observed_at=NOW,
            id=UUID(int=301),
        ),
        PriceHistory(
            asset_id=asset.id,
            price=Decimal("20.2000"),
            currency=Currency.TRY,
            observed_at=NOW,
            id=UUID(int=302),
        ),
    ]
    snapshots = [
        Snapshot(
            portfolio_id=portfolio.id,
            total_value=Decimal("100.1000"),
            currency=Currency.TRY,
            captured_at=NOW,
            id=UUID(int=401),
        ),
        Snapshot(
            portfolio_id=portfolio.id,
            total_value=Decimal("200.2000"),
            currency=Currency.TRY,
            captured_at=NOW,
            id=UUID(int=402),
        ),
    ]
    session = session_factory()
    history_repository = SQLAlchemyPriceHistoryRepository(session)
    snapshot_repository = SQLAlchemySnapshotRepository(session)
    try:
        SQLAlchemyPortfolioRepository(session).add(portfolio)
        for history in reversed(histories):
            history_repository.add(history)
        for snapshot in reversed(snapshots):
            snapshot_repository.add(snapshot)

        assert [item.id for item in history_repository.list()] == [
            history.id for history in histories
        ]
        assert [item.id for item in snapshot_repository.list()] == [
            snapshot.id for snapshot in snapshots
        ]
        assert all(isinstance(item, PriceHistory) for item in history_repository.list())
        assert all(isinstance(item, Snapshot) for item in snapshot_repository.list())
        assert history_repository.get(uuid4()) is None
        assert snapshot_repository.get(uuid4()) is None
        assert history_repository.exists(histories[0].id)
        assert snapshot_repository.exists(snapshots[0].id)
        assert history_repository.count() == 2
        assert snapshot_repository.count() == 2

        history_repository.delete(
            PriceHistory(
                asset_id=asset.id,
                price=Decimal("1"),
                currency=Currency.TRY,
                observed_at=NOW,
                id=UUID(int=999),
            )
        )
        snapshot_repository.delete(
            Snapshot(
                portfolio_id=portfolio.id,
                total_value=Decimal("1"),
                currency=Currency.TRY,
                captured_at=NOW,
                id=UUID(int=999),
            )
        )
        assert history_repository.count() == 2
        assert snapshot_repository.count() == 2

        history_repository.delete(histories[1])
        snapshot_repository.delete(snapshots[1])
        assert history_repository.get(histories[1].id) is None
        assert snapshot_repository.get(snapshots[1].id) is None
        assert history_repository.count() == 1
        assert snapshot_repository.count() == 1
    finally:
        session.rollback()
        session.close()


def test_missing_parent_foreign_key_uses_repository_error(
    session_factory: sessionmaker[Session],
) -> None:
    history = PriceHistory(
        asset_id=uuid4(),
        price=Decimal("1.00"),
        currency=Currency.TRY,
        observed_at=NOW,
    )
    session = session_factory()
    try:
        with pytest.raises(RepositoryError) as raised:
            SQLAlchemyPriceHistoryRepository(session).add(history)
        assert raised.value.__cause__ is not None
    finally:
        session.rollback()
        session.close()


def test_portfolio_delete_cascades_owned_rows_but_preserves_asset(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    portfolio = make_portfolio(asset)
    snapshot = Snapshot(
        portfolio_id=portfolio.id,
        total_value=Decimal("100.00"),
        currency=Currency.TRY,
        captured_at=NOW,
    )
    writer = session_factory()
    portfolio_repository = SQLAlchemyPortfolioRepository(writer)
    portfolio_repository.add(portfolio)
    SQLAlchemySnapshotRepository(writer).add(snapshot)
    writer.commit()
    portfolio_repository.delete(portfolio)
    writer.commit()
    writer.close()

    reader = session_factory()
    try:
        assert SQLAlchemyPortfolioRepository(reader).get(portfolio.id) is None
        assert SQLAlchemySnapshotRepository(reader).get(snapshot.id) is None
        assert SQLAlchemyAssetRepository(reader).get(asset.id) == asset
        transaction_count = reader.scalar(
            text("SELECT COUNT(*) FROM transactions WHERE portfolio_id = :id"),
            {"id": portfolio.id.hex},
        )
        assert transaction_count == 0
    finally:
        reader.close()
