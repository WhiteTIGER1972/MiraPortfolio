"""Integration tests for deterministic latest Asset price persistence queries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.price_history import PriceHistory
from app.domain.value_objects.currency import Currency
from app.infrastructure.persistence.sqlalchemy.repositories import (
    SQLAlchemyAssetRepository,
    SQLAlchemyPriceHistoryRepository,
)

OBSERVED_AT = datetime(2026, 7, 20, 9, 15, 30, 123456, tzinfo=UTC)


def make_asset(
    *,
    identity: UUID,
    symbol: str = "PRICE",
    currency: Currency = Currency.USD,
) -> Asset:
    return Asset(
        symbol=symbol,
        name=f"{symbol} Asset {identity.int}",
        asset_type=AssetType.EQUITY,
        currency=currency,
        id=identity,
        created_at=OBSERVED_AT,
    )


def make_price(
    asset: Asset,
    *,
    identity: UUID,
    price: Decimal,
    observed_at: datetime,
) -> PriceHistory:
    return PriceHistory(
        asset_id=asset.id,
        price=price,
        currency=asset.currency,
        observed_at=observed_at,
        id=identity,
    )


def persist(
    session_factory: sessionmaker[Session],
    *,
    assets: tuple[Asset, ...],
    prices: tuple[PriceHistory, ...],
) -> None:
    writer = session_factory()
    try:
        asset_repository = SQLAlchemyAssetRepository(writer)
        price_repository = SQLAlchemyPriceHistoryRepository(writer)
        for asset in assets:
            asset_repository.add(asset)
        for price in prices:
            price_repository.add(price)
        writer.commit()
    finally:
        writer.close()


def test_latest_price_returns_none_when_asset_has_no_history(
    session_factory: sessionmaker[Session],
) -> None:
    reader = session_factory()
    try:
        assert SQLAlchemyPriceHistoryRepository(reader).get_latest_for_asset(UUID(int=999)) is None
    finally:
        reader.close()


def test_latest_price_round_trip_preserves_domain_identity_decimal_and_utc(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=UUID(int=10), currency=Currency.EUR)
    price = make_price(
        asset,
        identity=UUID(int=11),
        price=Decimal("123456789.000000000123400"),
        observed_at=OBSERVED_AT,
    )
    persist(session_factory, assets=(asset,), prices=(price,))

    reader = session_factory()
    try:
        result = SQLAlchemyPriceHistoryRepository(reader).get_latest_for_asset(asset.id)
    finally:
        reader.close()

    assert isinstance(result, PriceHistory)
    assert result == price
    assert result.id == price.id
    assert result.asset_id == asset.id
    assert result.currency is Currency.EUR
    assert result.price == Decimal("123456789.000000000123400")
    assert str(result.price) == "123456789.000000000123400"
    assert result.observed_at == OBSERVED_AT
    assert result.observed_at.tzinfo is UTC
    assert result.observed_at.microsecond == 123456


def test_latest_observed_at_has_priority_over_greater_uuid(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=UUID(int=20))
    earlier = make_price(
        asset,
        identity=UUID(int=999),
        price=Decimal("1"),
        observed_at=OBSERVED_AT - timedelta(hours=2),
    )
    latest = make_price(
        asset,
        identity=UUID(int=1),
        price=Decimal("3"),
        observed_at=OBSERVED_AT,
    )
    middle = make_price(
        asset,
        identity=UUID(int=500),
        price=Decimal("2"),
        observed_at=OBSERVED_AT - timedelta(hours=1),
    )
    persist(
        session_factory,
        assets=(asset,),
        prices=(earlier, latest, middle),
    )

    reader = session_factory()
    try:
        result = SQLAlchemyPriceHistoryRepository(reader).get_latest_for_asset(asset.id)
    finally:
        reader.close()

    assert result == latest


def test_equal_observed_at_uses_greatest_uuid_tie_break(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=UUID(int=30))
    smaller = make_price(
        asset,
        identity=UUID(int=1),
        price=Decimal("1"),
        observed_at=OBSERVED_AT,
    )
    greater = make_price(
        asset,
        identity=UUID(int=2),
        price=Decimal("2"),
        observed_at=OBSERVED_AT,
    )
    persist(
        session_factory,
        assets=(asset,),
        prices=(smaller, greater),
    )

    reader = session_factory()
    try:
        result = SQLAlchemyPriceHistoryRepository(reader).get_latest_for_asset(asset.id)
    finally:
        reader.close()

    assert result == greater


def test_latest_price_isolated_by_asset_uuid_even_with_duplicate_symbols(
    session_factory: sessionmaker[Session],
) -> None:
    first_asset = make_asset(identity=UUID(int=40), symbol="DUP")
    second_asset = make_asset(identity=UUID(int=41), symbol="dup")
    first_latest = make_price(
        first_asset,
        identity=UUID(int=42),
        price=Decimal("40.1000"),
        observed_at=OBSERVED_AT,
    )
    second_latest = make_price(
        second_asset,
        identity=UUID(int=43),
        price=Decimal("99.9000"),
        observed_at=OBSERVED_AT + timedelta(days=1),
    )
    persist(
        session_factory,
        assets=(first_asset, second_asset),
        prices=(first_latest, second_latest),
    )

    reader = session_factory()
    repository = SQLAlchemyPriceHistoryRepository(reader)
    try:
        assert repository.get_latest_for_asset(first_asset.id) == first_latest
        assert repository.get_latest_for_asset(second_asset.id) == second_latest
    finally:
        reader.close()

    assert first_asset.symbol == second_asset.symbol


def test_newer_zero_price_is_returned_as_latest(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=UUID(int=50))
    older = make_price(
        asset,
        identity=UUID(int=51),
        price=Decimal("75.2500"),
        observed_at=OBSERVED_AT - timedelta(minutes=1),
    )
    zero = make_price(
        asset,
        identity=UUID(int=52),
        price=Decimal("0"),
        observed_at=OBSERVED_AT,
    )
    persist(session_factory, assets=(asset,), prices=(older, zero))

    reader = session_factory()
    try:
        result = SQLAlchemyPriceHistoryRepository(reader).get_latest_for_asset(asset.id)
    finally:
        reader.close()

    assert result == zero
    assert result.price == Decimal("0")


def test_uncommitted_latest_price_is_not_durable(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=UUID(int=60))
    price = make_price(
        asset,
        identity=UUID(int=61),
        price=Decimal("60"),
        observed_at=OBSERVED_AT,
    )
    writer = session_factory()
    SQLAlchemyAssetRepository(writer).add(asset)
    SQLAlchemyPriceHistoryRepository(writer).add(price)
    writer.close()

    reader = session_factory()
    try:
        assert SQLAlchemyPriceHistoryRepository(reader).get_latest_for_asset(asset.id) is None
    finally:
        reader.close()


def test_rollback_preserves_previously_committed_latest_price(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=UUID(int=70))
    committed = make_price(
        asset,
        identity=UUID(int=71),
        price=Decimal("70"),
        observed_at=OBSERVED_AT,
    )
    rolled_back = make_price(
        asset,
        identity=UUID(int=72),
        price=Decimal("72"),
        observed_at=OBSERVED_AT + timedelta(minutes=1),
    )
    persist(session_factory, assets=(asset,), prices=(committed,))

    writer = session_factory()
    try:
        SQLAlchemyPriceHistoryRepository(writer).add(rolled_back)
        writer.rollback()
    finally:
        writer.close()

    reader = session_factory()
    try:
        result = SQLAlchemyPriceHistoryRepository(reader).get_latest_for_asset(asset.id)
    finally:
        reader.close()

    assert result == committed


def test_repeated_latest_reads_are_bounded_and_do_not_mutate(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset(identity=UUID(int=80))
    prices = (
        make_price(
            asset,
            identity=UUID(int=81),
            price=Decimal("80"),
            observed_at=OBSERVED_AT - timedelta(minutes=1),
        ),
        make_price(
            asset,
            identity=UUID(int=82),
            price=Decimal("82"),
            observed_at=OBSERVED_AT,
        ),
    )
    persist(session_factory, assets=(asset,), prices=prices)

    reader = session_factory()
    repository = SQLAlchemyPriceHistoryRepository(reader)
    asset_repository = SQLAlchemyAssetRepository(reader)
    price_count_before = repository.count()
    asset_count_before = asset_repository.count()
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
            statements.append(" ".join(statement.upper().split()))

    bind = reader.get_bind()
    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        first = repository.get_latest_for_asset(asset.id)
        second = repository.get_latest_for_asset(asset.id)
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)

    try:
        assert first == prices[1]
        assert second == first
        assert repository.count() == price_count_before
        assert asset_repository.count() == asset_count_before
        assert [price.id for price in repository.list(offset=0, limit=1)] == [prices[0].id]
    finally:
        reader.close()

    assert len(statements) == 2
    for statement in statements:
        assert "WHERE PRICE_HISTORY.ASSET_ID =" in statement
        assert "ORDER BY PRICE_HISTORY.OBSERVED_AT DESC, PRICE_HISTORY.ID DESC" in statement
        assert "LIMIT" in statement
