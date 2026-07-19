"""Portfolio application service contracts and default orchestration."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.application.commands import (
    BuyAssetCommand,
    CreatePortfolioCommand,
    DeleteTransactionCommand,
    SellAssetCommand,
)
from app.application.exceptions import AssetNotFoundError, PortfolioNotFoundError
from app.application.queries import GetPortfolioQuery, ListPortfoliosQuery
from app.application.results import (
    AssetPositionView,
    PortfolioDetails,
    PortfolioSummary,
    TransactionView,
)
from app.application.unit_of_work import UnitOfWork
from app.domain.entities.asset import Asset
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.transaction import Transaction, TransactionType


class PortfolioApplicationService(ABC):
    """Define portfolio use cases without business or persistence logic."""

    @abstractmethod
    def create_portfolio(self, command: CreatePortfolioCommand) -> PortfolioDetails:
        """Create a portfolio and return its details."""

    @abstractmethod
    def buy_asset(self, command: BuyAssetCommand) -> TransactionView:
        """Record an asset purchase and return the transaction."""

    @abstractmethod
    def sell_asset(self, command: SellAssetCommand) -> TransactionView:
        """Record an asset sale and return the transaction."""

    @abstractmethod
    def delete_transaction(self, command: DeleteTransactionCommand) -> PortfolioDetails:
        """Delete a transaction and return the updated portfolio details."""

    @abstractmethod
    def get_portfolio(self, query: GetPortfolioQuery) -> PortfolioDetails:
        """Return the requested portfolio details."""

    @abstractmethod
    def list_portfolios(self, query: ListPortfoliosQuery) -> tuple[PortfolioSummary, ...]:
        """Return the available portfolio summaries."""


class DefaultPortfolioApplicationService(PortfolioApplicationService):
    """Orchestrate portfolio use cases through an injected Unit of Work factory."""

    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def create_portfolio(self, command: CreatePortfolioCommand) -> PortfolioDetails:
        """Create and persist a new portfolio."""
        with self._unit_of_work_factory() as unit_of_work:
            portfolio = Portfolio(name=command.portfolio_name)
            persisted = unit_of_work.portfolios.add(portfolio)
            unit_of_work.commit()
            return _to_portfolio_details(persisted)

    def buy_asset(self, command: BuyAssetCommand) -> TransactionView:
        """Record and persist a purchase of an existing asset."""
        with self._unit_of_work_factory() as unit_of_work:
            portfolio = _require_portfolio(unit_of_work, command.portfolio_id)
            asset = _require_asset(unit_of_work, command.asset_id)
            transaction = _transaction_from_command(
                asset,
                command.quantity,
                command.unit_price,
                command.trade_datetime,
                TransactionType.BUY,
            )
            if not any(held_asset.id == asset.id for held_asset in portfolio.assets):
                portfolio.add_asset(asset)
            portfolio.record_transaction(transaction)
            unit_of_work.portfolios.save(portfolio)
            unit_of_work.commit()
            return _to_transaction_view(transaction)

    def sell_asset(self, command: SellAssetCommand) -> TransactionView:
        """Record and persist a sale of an existing portfolio asset."""
        with self._unit_of_work_factory() as unit_of_work:
            portfolio = _require_portfolio(unit_of_work, command.portfolio_id)
            asset = _require_asset(unit_of_work, command.asset_id)
            transaction = _transaction_from_command(
                asset,
                command.quantity,
                command.unit_price,
                command.trade_datetime,
                TransactionType.SELL,
            )
            portfolio.record_transaction(transaction)
            unit_of_work.portfolios.save(portfolio)
            unit_of_work.commit()
            return _to_transaction_view(transaction)

    def delete_transaction(self, command: DeleteTransactionCommand) -> PortfolioDetails:
        """Remove and persist an owned transaction."""
        with self._unit_of_work_factory() as unit_of_work:
            portfolio = _require_portfolio(unit_of_work, command.portfolio_id)
            portfolio.remove_transaction(command.transaction_id)
            persisted = unit_of_work.portfolios.save(portfolio)
            unit_of_work.commit()
            return _to_portfolio_details(persisted)

    def get_portfolio(self, query: GetPortfolioQuery) -> PortfolioDetails:
        """Load and map one portfolio without committing."""
        with self._unit_of_work_factory() as unit_of_work:
            portfolio = _require_portfolio(unit_of_work, query.portfolio_id)
            return _to_portfolio_details(portfolio)

    def list_portfolios(self, query: ListPortfoliosQuery) -> tuple[PortfolioSummary, ...]:
        """Load and map the repository-defined portfolio page without committing."""
        del query
        with self._unit_of_work_factory() as unit_of_work:
            return tuple(
                _to_portfolio_summary(portfolio) for portfolio in unit_of_work.portfolios.list()
            )


def _require_portfolio(unit_of_work: UnitOfWork, portfolio_id: UUID) -> Portfolio:
    portfolio = unit_of_work.portfolios.get(portfolio_id)
    if portfolio is None:
        raise PortfolioNotFoundError(f"Portfolio {portfolio_id} was not found.")
    return portfolio


def _require_asset(unit_of_work: UnitOfWork, asset_id: UUID) -> Asset:
    asset = unit_of_work.assets.get(asset_id)
    if asset is None:
        raise AssetNotFoundError(f"Asset {asset_id} was not found.")
    return asset


def _transaction_from_command(
    asset: Asset,
    quantity: Decimal,
    unit_price: Decimal,
    trade_datetime: datetime,
    transaction_type: TransactionType,
) -> Transaction:
    return Transaction(
        asset_id=asset.id,
        quantity=quantity,
        price=unit_price,
        transaction_type=transaction_type,
        date=trade_datetime,
    )


def _to_portfolio_summary(portfolio: Portfolio) -> PortfolioSummary:
    return PortfolioSummary(
        id=portfolio.id,
        name=portfolio.name,
        base_currency=portfolio.base_currency,
        is_archived=portfolio.is_archived,
        created_at=portfolio.created_at,
    )


def _to_portfolio_details(portfolio: Portfolio) -> PortfolioDetails:
    return PortfolioDetails(
        id=portfolio.id,
        name=portfolio.name,
        base_currency=portfolio.base_currency,
        assets=tuple(_to_asset_position_view(asset) for asset in portfolio.assets),
        transactions=tuple(
            _to_transaction_view(transaction) for transaction in portfolio.transactions
        ),
        is_archived=portfolio.is_archived,
        created_at=portfolio.created_at,
    )


def _to_transaction_view(transaction: Transaction) -> TransactionView:
    return TransactionView(
        id=transaction.id,
        asset_id=transaction.asset_id,
        quantity=transaction.quantity,
        price=transaction.price,
        transaction_type=transaction.transaction_type,
        commission=transaction.commission,
        tax=transaction.tax,
        date=transaction.date,
    )


def _to_asset_position_view(asset: Asset) -> AssetPositionView:
    return AssetPositionView(
        id=asset.id,
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type,
        currency=asset.currency,
        is_active=asset.is_active,
        created_at=asset.created_at,
    )


__all__ = ["DefaultPortfolioApplicationService", "PortfolioApplicationService"]
