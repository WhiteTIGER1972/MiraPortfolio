"""Metadata tests for the supported persistence schema."""

from sqlalchemy import CheckConstraint, Float, PrimaryKeyConstraint, UniqueConstraint

from app.infrastructure.persistence.sqlalchemy import models
from app.infrastructure.persistence.sqlalchemy.base import Base, ExactDecimal, UTCDateTime

del models

EXPECTED_TABLES = {
    "assets",
    "portfolio_assets",
    "portfolios",
    "price_history",
    "snapshots",
    "transactions",
}

EXPECTED_PRIMARY_KEYS = {
    "assets": ("id",),
    "portfolios": ("id",),
    "portfolio_assets": ("portfolio_id", "asset_id"),
    "transactions": ("id",),
    "price_history": ("id",),
    "snapshots": ("id",),
}

EXPECTED_FOREIGN_KEYS = {
    ("portfolio_assets", "portfolio_id"): ("portfolios.id", "CASCADE"),
    ("portfolio_assets", "asset_id"): ("assets.id", "RESTRICT"),
    ("transactions", "portfolio_id"): ("portfolios.id", "CASCADE"),
    ("transactions", "asset_id"): ("assets.id", "RESTRICT"),
    ("price_history", "asset_id"): ("assets.id", "CASCADE"),
    ("snapshots", "portfolio_id"): ("portfolios.id", "CASCADE"),
}

FINANCIAL_COLUMNS = {
    ("transactions", "quantity"),
    ("transactions", "price"),
    ("transactions", "commission"),
    ("transactions", "tax"),
    ("price_history", "price"),
    ("snapshots", "total_value"),
}

UTC_COLUMNS = {
    ("assets", "created_at"),
    ("portfolios", "created_at"),
    ("transactions", "date"),
    ("price_history", "observed_at"),
    ("snapshots", "captured_at"),
}


def test_metadata_contains_exact_supported_table_and_primary_key_scope() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    for table_name, primary_key in EXPECTED_PRIMARY_KEYS.items():
        table = Base.metadata.tables[table_name]
        assert tuple(column.name for column in table.primary_key.columns) == primary_key
        assert table.primary_key.name == f"pk_{table_name}"


def test_metadata_foreign_keys_nullability_and_ordering_are_explicit() -> None:
    actual_foreign_keys = {
        (table.name, foreign_key.parent.name): (
            foreign_key.target_fullname,
            foreign_key.ondelete,
        )
        for table in Base.metadata.tables.values()
        for foreign_key in table.foreign_keys
    }

    assert actual_foreign_keys == EXPECTED_FOREIGN_KEYS
    assert all(
        column.nullable is False
        for table in Base.metadata.tables.values()
        for column in table.columns
    )
    assert "position" in Base.metadata.tables["portfolio_assets"].c
    assert "position" in Base.metadata.tables["transactions"].c


def test_metadata_constraints_are_named_and_match_domain_invariants() -> None:
    checks = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    unique_constraints = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and not isinstance(constraint, PrimaryKeyConstraint)
    }

    assert checks == {
        "ck_assets_asset_type",
        "ck_assets_currency",
        "ck_portfolios_base_currency",
        "ck_portfolio_assets_position",
        "ck_transactions_transaction_type",
        "ck_transactions_position",
        "ck_price_history_currency",
        "ck_snapshots_currency",
    }
    assert unique_constraints == {
        "uq_portfolio_assets_portfolio_position",
        "uq_transactions_portfolio_position",
    }


def test_financial_and_utc_columns_use_exact_adapters_without_float_types() -> None:
    for table_name, column_name in FINANCIAL_COLUMNS:
        column_type = Base.metadata.tables[table_name].c[column_name].type
        assert isinstance(column_type, ExactDecimal)
        assert not isinstance(column_type, Float)
        assert "REAL" not in str(column_type).upper()
        assert "FLOAT" not in str(column_type).upper()

    for table_name, column_name in UTC_COLUMNS:
        assert isinstance(Base.metadata.tables[table_name].c[column_name].type, UTCDateTime)

    assert all(not table.indexes for table in Base.metadata.tables.values())
