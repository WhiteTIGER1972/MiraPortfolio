"""Explicit SQLAlchemy engine and session factories."""

from sqlite3 import Connection as SQLiteConnection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def create_persistence_engine(database_url: str) -> Engine:
    """Create a configurable engine without opening a connection at import time."""
    url = make_url(database_url)
    is_sqlite = url.get_backend_name() == "sqlite"
    is_memory_sqlite = is_sqlite and url.database in {None, "", ":memory:"}

    options: dict[str, object] = {"pool_pre_ping": True}
    if is_sqlite:
        options["connect_args"] = {"check_same_thread": False}
    if is_memory_sqlite:
        options["poolclass"] = StaticPool

    engine = create_engine(database_url, **options)
    if is_sqlite:
        _configure_sqlite(engine, use_wal=not is_memory_sqlite)
    return engine


def _configure_sqlite(engine: Engine, *, use_wal: bool) -> None:
    """Enable deterministic SQLite integrity and connection behavior."""

    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection: SQLiteConnection, _: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            if use_wal:
                cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
        finally:
            cursor.close()


def create_persistence_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions with explicit flush/commit and non-expiring domain reads."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        close_resets_only=False,
    )


__all__ = ["create_persistence_engine", "create_persistence_session_factory"]
