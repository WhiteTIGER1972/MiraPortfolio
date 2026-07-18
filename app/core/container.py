"""Dependency injection composition root placeholder."""

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.infrastructure.database import DatabaseManager


@dataclass(frozen=True, slots=True)
class Container:
    """Container for infrastructure dependencies and future service registrations."""

    settings: Settings
    database_manager: DatabaseManager
    session_factory: sessionmaker[Session]
