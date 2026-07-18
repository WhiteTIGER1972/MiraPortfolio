"""SQLAlchemy database lifecycle management."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from sqlite3 import Connection as SQLiteConnection
from typing import Self

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import DatabaseError
from app.core.settings import Settings
from app.infrastructure.persistence.sqlalchemy.base import Base


class DatabaseManager:
    """Own database engine, sessions, migrations, health, and shutdown lifecycle."""

    def __init__(self, settings: Settings) -> None:
        """Create an uninitialized manager for the provided application settings."""
        self._settings = settings
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    @property
    def engine(self) -> Engine:
        """Return the initialized SQLAlchemy engine."""
        if self._engine is None:
            raise DatabaseError("DatabaseManager has not been initialized.")
        return self._engine

    @property
    def session_factory(self) -> sessionmaker[Session]:
        """Return the initialized session factory."""
        if self._session_factory is None:
            raise DatabaseError("DatabaseManager has not been initialized.")
        return self._session_factory

    def initialize(self) -> Self:
        """Create the engine, initialize tables, and build the session factory."""
        if self._engine is not None:
            return self

        try:
            self._engine = create_database_engine(self._settings)
            initialize_database(self._engine)
            self._session_factory = create_session_factory(self._engine)
        except SQLAlchemyError as error:
            self.shutdown()
            raise DatabaseError("Database initialization failed.") from error
        return self

    def health_check(self) -> bool:
        """Return whether the database accepts a lightweight query."""
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return False
        return True

    def migrate(self, revision: str = "head") -> None:
        """Upgrade the database schema to the requested Alembic revision."""
        project_root = Path(__file__).resolve().parents[2]
        config = Config(str(project_root / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", self._settings.database_url)
        try:
            command.upgrade(config, revision)
        except Exception as error:
            raise DatabaseError(f"Database migration to {revision!r} failed.") from error

    def shutdown(self) -> None:
        """Dispose active connections and release database resources."""
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._session_factory = None


def create_database_engine(settings: Settings) -> Engine:
    """Create an engine and configure SQLite connections for desktop use."""
    is_sqlite = settings.database_url.startswith("sqlite")
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False} if is_sqlite else {},
        pool_pre_ping=True,
    )
    if is_sqlite:
        _configure_sqlite(engine)
    return engine


def _configure_sqlite(engine: Engine) -> None:
    """Enable SQLite foreign keys, WAL journaling, and normal synchronization."""

    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection: SQLiteConnection, _: object) -> None:
        """Apply required SQLite PRAGMA settings to each new connection."""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
        finally:
            cursor.close()


def initialize_database(engine: Engine) -> None:
    """Create registered tables for a first application run."""
    try:
        Base.metadata.create_all(bind=engine)
    except SQLAlchemyError as error:
        raise DatabaseError("Database table initialization failed.") from error


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a configured SQLAlchemy session factory."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        close_resets_only=False,
    )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield an explicit session and roll back any uncommitted transaction."""
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        if session.in_transaction():
            session.rollback()
        session.close()
