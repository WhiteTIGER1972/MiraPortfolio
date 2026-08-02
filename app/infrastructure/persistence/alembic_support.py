"""Deterministic Alembic configuration and metadata validation support."""

from pathlib import Path
from typing import Final

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, Connection, UniqueConstraint, inspect

from app.core.exceptions import DatabaseError
from app.infrastructure.persistence.sqlalchemy import models
from app.infrastructure.persistence.sqlalchemy.base import Base

del models


def _immutable_resource_root(module_file: Path) -> Path:
    """Resolve the source root or frozen ``_internal`` resource root."""
    return module_file.resolve().parents[3]


PROJECT_ROOT: Final = _immutable_resource_root(Path(__file__))
DEFAULT_MIGRATION_SCRIPT_LOCATION: Final = PROJECT_ROOT / "migrations"


def create_alembic_config(
    database_url: str,
    *,
    script_location: Path | None = None,
) -> Config:
    """Create deterministic Alembic configuration without changing repository files."""
    location = (script_location or DEFAULT_MIGRATION_SCRIPT_LOCATION).resolve()
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(location))
    config.set_main_option("prepend_sys_path", str(PROJECT_ROOT))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def run_upgrade(config: Config, connection: Connection) -> None:
    """Upgrade through the exact externally owned SQLAlchemy connection."""
    if connection.in_transaction():
        connection.rollback()
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


def run_stamp(config: Config, connection: Connection, head: str) -> None:
    """Stamp an exactly compatible schema through the supplied connection."""
    if connection.in_transaction():
        connection.rollback()
    config.attributes["connection"] = connection
    command.stamp(config, head, purge=True)


def require_single_head(config: Config) -> tuple[ScriptDirectory, str]:
    """Load the migration graph and require one unambiguous head."""
    try:
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()
    except Exception as error:
        raise DatabaseError("Alembic migration scripts could not be loaded.") from error
    if len(heads) != 1:
        raise DatabaseError("The migration directory must contain exactly one head revision.")
    return script, heads[0]


def validated_current_revision(
    connection: Connection,
    script: ScriptDirectory,
) -> str | None:
    """Return the current revision only when it belongs to the migration graph."""
    revisions = MigrationContext.configure(connection).get_current_heads()
    if len(revisions) > 1:
        raise DatabaseError("The database contains multiple Alembic revisions.")
    if not revisions:
        return None

    revision = revisions[0]
    known_revisions = {candidate.revision for candidate in script.walk_revisions()}
    if revision not in known_revisions:
        raise DatabaseError("The database contains an unknown Alembic revision.")
    return revision


def verify_revision(connection: Connection, expected_head: str) -> None:
    """Require the database's stored revision to equal the single head."""
    revisions = MigrationContext.configure(connection).get_current_heads()
    if revisions != (expected_head,):
        raise DatabaseError("The database did not reach the required Alembic revision.")


def validate_schema(connection: Connection) -> None:
    """Require material equivalence between the database and current ORM metadata."""
    inspector = inspect(connection)
    actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
    expected_tables = set(Base.metadata.tables)
    if actual_tables != expected_tables:
        raise DatabaseError("The database schema is incompatible with this release.")

    for table_name in sorted(expected_tables):
        expected_table = Base.metadata.tables[table_name]
        actual_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        if set(actual_columns) != set(expected_table.columns.keys()):
            raise DatabaseError("The database schema is incompatible with this release.")
        for expected_column in expected_table.columns:
            actual_column = actual_columns[expected_column.name]
            actual_type = actual_column["type"].compile(dialect=connection.dialect)
            expected_type = expected_column.type.compile(dialect=connection.dialect)
            if _normalize_sql(actual_type) != _normalize_sql(expected_type):
                raise DatabaseError("The database schema is incompatible with this release.")
            if bool(actual_column["nullable"]) is not expected_column.nullable:
                raise DatabaseError("The database schema is incompatible with this release.")

        actual_primary_key = inspector.get_pk_constraint(table_name)
        if tuple(actual_primary_key["constrained_columns"]) != tuple(
            column.name for column in expected_table.primary_key.columns
        ):
            raise DatabaseError("The database schema is incompatible with this release.")
        if actual_primary_key.get("name") != expected_table.primary_key.name:
            raise DatabaseError("The database schema is incompatible with this release.")

        actual_foreign_keys = {
            (
                constraint.get("name"),
                tuple(constraint["constrained_columns"]),
                constraint["referred_table"],
                tuple(constraint["referred_columns"]),
                constraint["options"].get("ondelete"),
            )
            for constraint in inspector.get_foreign_keys(table_name)
        }
        expected_foreign_keys = {
            (
                constraint.name,
                tuple(column.name for column in constraint.columns),
                next(iter(constraint.elements)).column.table.name,
                tuple(element.column.name for element in constraint.elements),
                constraint.ondelete,
            )
            for constraint in expected_table.foreign_key_constraints
        }
        if actual_foreign_keys != expected_foreign_keys:
            raise DatabaseError("The database schema is incompatible with this release.")

        actual_unique = {
            (
                constraint.get("name"),
                tuple(constraint["column_names"]),
            )
            for constraint in inspector.get_unique_constraints(table_name)
        }
        expected_unique = {
            (
                constraint.name,
                tuple(column.name for column in constraint.columns),
            )
            for constraint in expected_table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        if actual_unique != expected_unique:
            raise DatabaseError("The database schema is incompatible with this release.")

        actual_indexes = {
            (
                index.get("name"),
                tuple(index["column_names"]),
                bool(index["unique"]),
            )
            for index in inspector.get_indexes(table_name)
        }
        expected_indexes = {
            (
                index.name,
                tuple(column.name for column in index.columns),
                index.unique,
            )
            for index in expected_table.indexes
        }
        if actual_indexes != expected_indexes:
            raise DatabaseError("The database schema is incompatible with this release.")

        actual_checks = {
            (
                constraint.get("name"),
                _normalize_sql(constraint["sqltext"]),
            )
            for constraint in inspector.get_check_constraints(table_name)
        }
        expected_checks = {
            (
                constraint.name,
                _normalize_sql(constraint.sqltext),
            )
            for constraint in expected_table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        if actual_checks != expected_checks:
            raise DatabaseError("The database schema is incompatible with this release.")

    context = MigrationContext.configure(connection, opts={"compare_type": True})
    if compare_metadata(context, Base.metadata):
        raise DatabaseError("The database schema is incompatible with this release.")


def _normalize_sql(value: object) -> str:
    return "".join(str(value).split()).lower().replace('"', "").replace("`", "")


__all__ = [
    "DEFAULT_MIGRATION_SCRIPT_LOCATION",
    "create_alembic_config",
    "require_single_head",
    "run_stamp",
    "run_upgrade",
    "validate_schema",
    "validated_current_revision",
    "verify_revision",
]
