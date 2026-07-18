"""Integration tests for the initial Alembic persistence migration."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import (
    CheckConstraint,
    Engine,
    PrimaryKeyConstraint,
    UniqueConstraint,
    create_engine,
    inspect,
)

from app.infrastructure.persistence.sqlalchemy import models
from app.infrastructure.persistence.sqlalchemy.base import Base

del models

EXPECTED_TABLES = {
    "assets",
    "portfolio_assets",
    "portfolios",
    "price_history",
    "snapshots",
    "transactions",
}

EXPECTED_COLUMNS = {
    "assets": {
        "id",
        "symbol",
        "name",
        "asset_type",
        "currency",
        "is_active",
        "created_at",
    },
    "portfolios": {
        "id",
        "name",
        "base_currency",
        "is_archived",
        "created_at",
    },
    "portfolio_assets": {"portfolio_id", "asset_id", "position"},
    "transactions": {
        "id",
        "portfolio_id",
        "asset_id",
        "position",
        "quantity",
        "price",
        "transaction_type",
        "commission",
        "tax",
        "date",
    },
    "price_history": {"id", "asset_id", "price", "currency", "observed_at"},
    "snapshots": {
        "id",
        "portfolio_id",
        "total_value",
        "currency",
        "captured_at",
    },
}


def migration_config(database: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    return config


def inspected_engine(database: Path) -> Engine:
    return create_engine(f"sqlite:///{database.as_posix()}")


def test_upgrade_empty_database_creates_expected_tables_and_columns(
    tmp_path: Path,
) -> None:
    database = tmp_path / "upgrade.db"
    command.upgrade(migration_config(database), "head")
    engine = inspected_engine(database)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert tables == EXPECTED_TABLES
        for table_name, columns in EXPECTED_COLUMNS.items():
            assert {column["name"] for column in inspector.get_columns(table_name)} == columns
    finally:
        engine.dispose()


def test_migration_constraints_match_metadata(tmp_path: Path) -> None:
    database = tmp_path / "constraints.db"
    command.upgrade(migration_config(database), "head")
    engine = inspected_engine(database)
    try:
        inspector = inspect(engine)
        for table_name in EXPECTED_TABLES:
            table = Base.metadata.tables[table_name]
            assert inspector.get_pk_constraint(table_name)["name"] == table.primary_key.name
            expected_foreign_keys = {
                (
                    constraint.name,
                    tuple(column.name for column in constraint.columns),
                    next(iter(constraint.elements)).column.table.name,
                    tuple(element.column.name for element in constraint.elements),
                    constraint.ondelete,
                )
                for constraint in table.foreign_key_constraints
            }
            actual_foreign_keys = {
                (
                    constraint["name"],
                    tuple(constraint["constrained_columns"]),
                    constraint["referred_table"],
                    tuple(constraint["referred_columns"]),
                    constraint["options"].get("ondelete"),
                )
                for constraint in inspector.get_foreign_keys(table_name)
            }
            assert actual_foreign_keys == expected_foreign_keys
            expected_unique = {
                constraint.name
                for constraint in table.constraints
                if isinstance(constraint, UniqueConstraint)
                and not isinstance(constraint, PrimaryKeyConstraint)
            }
            actual_unique = {
                constraint["name"] for constraint in inspector.get_unique_constraints(table_name)
            }
            assert actual_unique == expected_unique
            expected_checks = {
                constraint.name
                for constraint in table.constraints
                if isinstance(constraint, CheckConstraint)
            }
            actual_checks = {
                constraint["name"] for constraint in inspector.get_check_constraints(table_name)
            }
            assert actual_checks == expected_checks
            expected_indexes = {
                (index.name, tuple(column.name for column in index.columns), index.unique)
                for index in table.indexes
            }
            actual_indexes = {
                (
                    index["name"],
                    tuple(index["column_names"]),
                    bool(index["unique"]),
                )
                for index in inspector.get_indexes(table_name)
            }
            assert actual_indexes == expected_indexes
    finally:
        engine.dispose()


def test_migration_column_types_and_nullability_match_metadata(
    tmp_path: Path,
) -> None:
    database = tmp_path / "types.db"
    command.upgrade(migration_config(database), "head")
    engine = inspected_engine(database)
    try:
        inspector = inspect(engine)
        for table_name in EXPECTED_TABLES:
            expected = Base.metadata.tables[table_name]
            actual_columns = {
                column["name"]: column for column in inspector.get_columns(table_name)
            }
            for column in expected.columns:
                actual = actual_columns[column.name]
                assert actual["nullable"] is column.nullable
                assert str(actual["type"]) == column.type.compile(dialect=engine.dialect)
                if column.name in {
                    "quantity",
                    "price",
                    "commission",
                    "tax",
                    "total_value",
                }:
                    assert "REAL" not in str(actual["type"]).upper()
                    assert "FLOAT" not in str(actual["type"]).upper()
    finally:
        engine.dispose()


def test_alembic_head_has_no_differences_from_orm_metadata(tmp_path: Path) -> None:
    database = tmp_path / "comparison.db"
    command.upgrade(migration_config(database), "head")
    engine = inspected_engine(database)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True},
            )
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def test_downgrade_removes_schema_and_repeat_upgrade_succeeds(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cycle.db"
    config = migration_config(database)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = inspected_engine(database)
    try:
        assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set()
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = inspected_engine(database)
    try:
        assert set(inspect(engine).get_table_names()) - {"alembic_version"} == EXPECTED_TABLES
    finally:
        engine.dispose()
