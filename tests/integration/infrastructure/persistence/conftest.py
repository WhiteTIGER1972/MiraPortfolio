"""Isolated SQLite fixtures for persistence integration tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.infrastructure.persistence.database_backup import SQLiteBackupService
from app.infrastructure.persistence.database_preparation import prepare_database
from app.infrastructure.persistence.sqlalchemy import models
from app.infrastructure.persistence.sqlalchemy.base import Base
from app.infrastructure.persistence.sqlalchemy.session import (
    create_persistence_engine,
    create_persistence_session_factory,
)

del models


@pytest.fixture
def backup_settings(tmp_path: Path) -> Settings:
    """Return settings with a current SQLite database and isolated backup directory."""
    root = tmp_path / "backup-fixture"
    root.mkdir()
    database = root / "portfolio.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{database.as_posix()}",
        database_path=database,
        database_directory=root,
        backup_directory=root / "backups",
    )
    prepare_database(settings, legacy_search_directory=root)
    return settings


@pytest.fixture
def backup_service(backup_settings: Settings) -> SQLiteBackupService:
    """Return the real backup implementation for an isolated current database."""
    return SQLiteBackupService(backup_settings)


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """Return an isolated file-backed SQLite URL."""
    return f"sqlite:///{(tmp_path / 'persistence.db').as_posix()}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    """Create and dispose an isolated schema."""
    persistence_engine = create_persistence_engine(database_url)
    Base.metadata.create_all(persistence_engine)
    try:
        yield persistence_engine
    finally:
        persistence_engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return the explicit session factory used by repositories and UoW tests."""
    return create_persistence_session_factory(engine)
