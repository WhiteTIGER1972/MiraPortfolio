"""Integration tests for explicit SQLAlchemy session lifecycle."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import DatabaseError
from app.core.settings import Settings
from app.domain.entities.asset import Asset, AssetType
from app.domain.value_objects.currency import Currency
from app.infrastructure.database import DatabaseManager, session_scope
from app.infrastructure.persistence.sqlalchemy.mappers import asset_to_model
from app.infrastructure.persistence.sqlalchemy.models import AssetModel


def make_asset() -> Asset:
    return Asset(
        symbol="SESSION",
        name="Session Asset",
        asset_type=AssetType.EQUITY,
        currency=Currency.TRY,
        id=uuid4(),
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
    )


def test_session_factory_creates_independent_sessions(
    session_factory: sessionmaker[Session],
) -> None:
    first = session_factory()
    second = session_factory()
    try:
        assert first is not second
        assert first.bind is second.bind
    finally:
        first.close()
        second.close()


def test_commit_makes_changes_visible_to_later_session(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    writer = session_factory()
    writer.add(asset_to_model(asset))
    writer.commit()
    writer.close()

    reader = session_factory()
    try:
        assert reader.get(AssetModel, asset.id) is not None
    finally:
        reader.close()


def test_rollback_discards_changes(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    writer = session_factory()
    writer.add(asset_to_model(asset))
    writer.flush()
    writer.rollback()
    writer.close()

    reader = session_factory()
    try:
        assert reader.get(AssetModel, asset.id) is None
    finally:
        reader.close()


def test_close_is_deterministic_and_non_reusable(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    session.close()

    with pytest.raises(InvalidRequestError, match="permanently closed"):
        session.scalars(select(AssetModel)).all()


def test_sqlite_foreign_keys_are_enabled(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    try:
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
    finally:
        session.close()


def test_session_scope_does_not_commit_implicitly(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    with session_scope(session_factory) as session:
        session.add(asset_to_model(asset))
        session.flush()

    reader = session_factory()
    try:
        assert reader.get(AssetModel, asset.id) is None
    finally:
        reader.close()


def test_session_scope_allows_explicit_commit(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    with session_scope(session_factory) as session:
        session.add(asset_to_model(asset))
        session.commit()

    reader = session_factory()
    try:
        assert reader.get(AssetModel, asset.id) is not None
    finally:
        reader.close()


def test_session_scope_rolls_back_exception_and_closes_session(
    session_factory: sessionmaker[Session],
) -> None:
    asset = make_asset()
    expected = RuntimeError("session use case failed")
    captured_session: Session | None = None

    with pytest.raises(RuntimeError) as raised:
        with session_scope(session_factory) as session:
            captured_session = session
            session.add(asset_to_model(asset))
            session.flush()
            raise expected

    assert raised.value is expected
    assert captured_session is not None
    with pytest.raises(InvalidRequestError, match="permanently closed"):
        captured_session.connection()
    reader = session_factory()
    try:
        assert reader.get(AssetModel, asset.id) is None
    finally:
        reader.close()


def test_database_manager_initializes_registered_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "manager.db"
    manager = DatabaseManager(
        Settings(database_url=f"sqlite:///{database.as_posix()}")
    ).initialize()
    try:
        assert manager.health_check()
        assert set(inspect(manager.engine).get_table_names()) == {
            "assets",
            "portfolio_assets",
            "portfolios",
            "price_history",
            "snapshots",
            "transactions",
        }
    finally:
        manager.shutdown()

    with pytest.raises(DatabaseError, match="not been initialized"):
        _ = manager.engine
