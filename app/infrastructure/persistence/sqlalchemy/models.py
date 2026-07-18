"""Typed SQLAlchemy models for the supported Sprint 1.4 domain scope."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.persistence.sqlalchemy.base import Base, ExactDecimal, UTCDateTime

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


class AssetModel(Base):
    """Persist one platform-neutral investment asset."""

    __tablename__ = "assets"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_assets"),
        CheckConstraint(
            f"asset_type IN {ASSET_TYPES}",
            name="ck_assets_asset_type",
        ),
        CheckConstraint(
            f"currency IN {CURRENCIES}",
            name="ck_assets_currency",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    portfolio_links: Mapped[list[PortfolioAssetModel]] = relationship(
        back_populates="asset",
        passive_deletes=True,
    )


class PortfolioModel(Base):
    """Persist one portfolio aggregate root."""

    __tablename__ = "portfolios"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_portfolios"),
        CheckConstraint(
            f"base_currency IN {CURRENCIES}",
            name="ck_portfolios_base_currency",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    asset_links: Mapped[list[PortfolioAssetModel]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="PortfolioAssetModel.position",
        passive_deletes=True,
    )
    transactions: Mapped[list[TransactionModel]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="TransactionModel.position",
        passive_deletes=True,
    )


class PortfolioAssetModel(Base):
    """Persist ordered asset membership in a portfolio."""

    __tablename__ = "portfolio_assets"
    __table_args__ = (
        PrimaryKeyConstraint(
            "portfolio_id",
            "asset_id",
            name="pk_portfolio_assets",
        ),
        CheckConstraint("position >= 0", name="ck_portfolio_assets_position"),
        UniqueConstraint(
            "portfolio_id",
            "position",
            name="uq_portfolio_assets_portfolio_position",
        ),
    )

    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "portfolios.id",
            name="fk_portfolio_assets_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        primary_key=True,
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "assets.id",
            name="fk_portfolio_assets_asset_id_assets",
            ondelete="RESTRICT",
        ),
        primary_key=True,
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    portfolio: Mapped[PortfolioModel] = relationship(back_populates="asset_links")
    asset: Mapped[AssetModel] = relationship(back_populates="portfolio_links", lazy="joined")


class TransactionModel(Base):
    """Persist one transaction owned by a portfolio aggregate."""

    __tablename__ = "transactions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_transactions"),
        CheckConstraint(
            f"transaction_type IN {TRANSACTION_TYPES}",
            name="ck_transactions_transaction_type",
        ),
        CheckConstraint("position >= 0", name="ck_transactions_position"),
        UniqueConstraint(
            "portfolio_id",
            "position",
            name="uq_transactions_portfolio_position",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "portfolios.id",
            name="fk_transactions_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "assets.id",
            name="fk_transactions_asset_id_assets",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    price: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    commission: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    tax: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    date: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    portfolio: Mapped[PortfolioModel] = relationship(back_populates="transactions")


class PriceHistoryModel(Base):
    """Persist one historical asset price."""

    __tablename__ = "price_history"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_price_history"),
        CheckConstraint(
            f"currency IN {CURRENCIES}",
            name="ck_price_history_currency",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, nullable=False)
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "assets.id",
            name="fk_price_history_asset_id_assets",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    price: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SnapshotModel(Base):
    """Persist one immutable portfolio valuation snapshot."""

    __tablename__ = "snapshots"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_snapshots"),
        CheckConstraint(
            f"currency IN {CURRENCIES}",
            name="ck_snapshots_currency",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "portfolios.id",
            name="fk_snapshots_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    total_value: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


__all__ = [
    "AssetModel",
    "PortfolioAssetModel",
    "PortfolioModel",
    "PriceHistoryModel",
    "SnapshotModel",
    "TransactionModel",
]
