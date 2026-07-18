"""Integration tests for explicit SQLAlchemy Unit of Work semantics."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.entities.asset import Asset, AssetType
from app.domain.value_objects.currency import Currency
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SQLAlchemyUnitOfWork

NOW = datetime(2026, 7, 18, tzinfo=UTC)


def make_asset() -> Asset:
    return Asset(
        symbol="UOW",
        name="Unit of Work Asset",
        asset_type=AssetType.EQUITY,
        currency=Currency.TRY,
        id=uuid4(),
        created_at=NOW,
    )


def test_inactive_unit_of_work_has_no_session_or_repository_access(
    session_factory: sessionmaker[Session],
) -> None:
    unit_of_work = SQLAlchemyUnitOfWork(session_factory)

    assert unit_of_work._session is None
    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.assets
    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.portfolios
    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.price_history
    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.snapshots
    with pytest.raises(RuntimeError, match="not active"):
        unit_of_work.commit()
    with pytest.raises(RuntimeError, match="not active"):
        unit_of_work.rollback()


def test_repositories_share_one_session(
    session_factory: sessionmaker[Session],
) -> None:
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        sessions = {
            unit_of_work.assets._session,
            unit_of_work.portfolios._session,
            unit_of_work.price_history._session,
            unit_of_work.snapshots._session,
        }
        assert len(sessions) == 1


def test_explicit_commit_persists(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.assets.add(asset)
        unit_of_work.commit()

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        assert reader.assets.get(asset.id) == asset


def test_missing_commit_does_not_persist(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.assets.add(asset)

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        assert reader.assets.get(asset.id) is None


def test_explicit_rollback_discards_changes(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.assets.add(asset)
        unit_of_work.rollback()

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        assert reader.assets.get(asset.id) is None


def test_exception_rolls_back_and_propagates_original(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    expected = RuntimeError("use case failed")
    captured_session: Session | None = None

    with pytest.raises(RuntimeError) as raised:
        with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
            unit_of_work.assets.add(asset)
            captured_session = unit_of_work._session
            assert captured_session is not None
            raise expected

    assert raised.value is expected
    assert captured_session is not None
    with pytest.raises(InvalidRequestError, match="permanently closed"):
        captured_session.connection()
    with SQLAlchemyUnitOfWork(session_factory) as reader:
        assert reader.assets.get(asset.id) is None


def test_session_is_closed_on_exit(
    session_factory: sessionmaker[Session],
) -> None:
    unit_of_work = SQLAlchemyUnitOfWork(session_factory)
    with unit_of_work:
        captured_session = unit_of_work._session
        captured_repository = unit_of_work.assets
        assert captured_session is not None

    assert unit_of_work._session is None
    assert unit_of_work._assets is None
    assert unit_of_work._portfolios is None
    assert unit_of_work._price_history is None
    assert unit_of_work._snapshots is None
    with pytest.raises(InvalidRequestError, match="permanently closed"):
        captured_session.connection()
    with pytest.raises(InvalidRequestError, match="permanently closed"):
        captured_repository.count()


def test_separate_units_of_work_are_isolated(
    session_factory: sessionmaker[Session],
) -> None:
    first_asset = make_asset()
    second_asset = make_asset()
    first = SQLAlchemyUnitOfWork(session_factory)
    second = SQLAlchemyUnitOfWork(session_factory)

    with first:
        with second:
            assert first._session is not None
            assert second._session is not None
            assert first._session is not second._session

    with first:
        first.assets.add(first_asset)
        first.commit()

    with second:
        assert second.assets.get(first_asset.id) == first_asset
        second.assets.add(second_asset)

    with SQLAlchemyUnitOfWork(session_factory) as reader:
        assert reader.assets.get(first_asset.id) == first_asset
        assert reader.assets.get(second_asset.id) is None


def test_unit_of_work_cannot_be_reentered(
    session_factory: sessionmaker[Session],
) -> None:
    unit_of_work = SQLAlchemyUnitOfWork(session_factory)
    with unit_of_work:
        with pytest.raises(RuntimeError, match="already active"):
            unit_of_work.__enter__()
