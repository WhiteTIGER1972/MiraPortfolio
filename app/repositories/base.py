"""Generic SQLAlchemy repository implementation."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import RepositoryError
from app.infrastructure.database import Base


class BaseRepository[ModelT: Base]:
    """Provide common persistence operations for a single SQLAlchemy model.

    Repositories flush changes but do not commit transactions. This lets an
    application service compose multiple repository operations atomically by
    using the shared session lifecycle from ``session_scope``.

    Args:
        session: SQLAlchemy session owned by the calling use case.
        model_type: ORM model class managed by this repository.
    """

    def __init__(self, session: Session, model_type: type[ModelT]) -> None:
        """Initialize the repository for one ORM model type."""
        self._session = session
        self._model_type = model_type

    def get(self, identity: Any) -> ModelT | None:
        """Return one entity by its primary key, or ``None`` when absent."""
        return self._session.get(self._model_type, identity)

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[ModelT]:
        """Return a paginated collection of entities.

        Args:
            offset: Number of records to skip.
            limit: Maximum number of records to return.

        Raises:
            ValueError: If pagination values are invalid.
        """
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive.")
        statement: Select[tuple[ModelT]] = select(self._model_type).offset(offset).limit(limit)
        return self._session.scalars(statement).all()

    def add(self, entity: ModelT) -> ModelT:
        """Attach an entity to the current unit of work and flush it."""
        self._session.add(entity)
        self._flush("create")
        return entity

    def delete(self, entity: ModelT) -> None:
        """Mark an entity for deletion and flush the current unit of work."""
        self._session.delete(entity)
        self._flush("delete")

    def exists(self, identity: Any) -> bool:
        """Return whether an entity with the supplied primary key exists."""
        return self.get(identity) is not None

    def count(self) -> int:
        """Return the total number of entities for this repository model."""
        statement = select(func.count()).select_from(self._model_type)
        return int(self._session.scalar(statement) or 0)

    def statement(self) -> Select[tuple[ModelT]]:
        """Return a base typed select statement for repository-specific queries."""
        return select(self._model_type)

    def _flush(self, operation: str) -> None:
        """Flush changes and convert persistence failures to domain exceptions."""
        try:
            self._session.flush()
        except SQLAlchemyError as error:
            raise RepositoryError(
                f"Could not {operation} {self._model_type.__name__} entity."
            ) from error
