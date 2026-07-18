"""Create the initial Sprint 1.4 persistence schema.

Revision ID: 20260718_0001
Revises:
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ASSET_TYPES = ("equity", "fund", "etf", "bond", "crypto", "cash")
CURRENCIES = ("TRY", "USD", "EUR", "GBP")
TRANSACTION_TYPES = (
    "buy",
    "sell",
    "dividend",
    "rights_issue",
    "bonus_issue",
    "stock_split",
)


def upgrade() -> None:
    """Create the supported persistence tables and constraints."""
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            f"asset_type IN {ASSET_TYPES}",
            name="ck_assets_asset_type",
        ),
        sa.CheckConstraint(
            f"currency IN {CURRENCIES}",
            name="ck_assets_currency",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assets"),
    )
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            f"base_currency IN {CURRENCIES}",
            name="ck_portfolios_base_currency",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_portfolios"),
    )
    op.create_table(
        "portfolio_assets",
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 0",
            name="ck_portfolio_assets_position",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_portfolio_assets_asset_id_assets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_portfolio_assets_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "portfolio_id",
            "asset_id",
            name="pk_portfolio_assets",
        ),
        sa.UniqueConstraint(
            "portfolio_id",
            "position",
            name="uq_portfolio_assets_portfolio_position",
        ),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.String(length=100), nullable=False),
        sa.Column("price", sa.String(length=100), nullable=False),
        sa.Column("transaction_type", sa.String(length=32), nullable=False),
        sa.Column("commission", sa.String(length=100), nullable=False),
        sa.Column("tax", sa.String(length=100), nullable=False),
        sa.Column("date", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            f"transaction_type IN {TRANSACTION_TYPES}",
            name="ck_transactions_transaction_type",
        ),
        sa.CheckConstraint("position >= 0", name="ck_transactions_position"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_transactions_asset_id_assets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_transactions_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
        sa.UniqueConstraint(
            "portfolio_id",
            "position",
            name="uq_transactions_portfolio_position",
        ),
    )
    op.create_table(
        "price_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.String(length=100), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("observed_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            f"currency IN {CURRENCIES}",
            name="ck_price_history_currency",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_price_history_asset_id_assets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_price_history"),
    )
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("total_value", sa.String(length=100), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("captured_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            f"currency IN {CURRENCIES}",
            name="ck_snapshots_currency",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_snapshots_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_snapshots"),
    )


def downgrade() -> None:
    """Drop the supported persistence tables in dependency order."""
    op.drop_table("snapshots")
    op.drop_table("price_history")
    op.drop_table("transactions")
    op.drop_table("portfolio_assets")
    op.drop_table("portfolios")
    op.drop_table("assets")
