"""Portfolio application service contracts and default orchestration."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.application.commands import (
    BuyAssetCommand,
    CreateAssetCommand,
    CreatePortfolioCommand,
    DeleteTransactionCommand,
    RecordMarketPriceCommand,
    SellAssetCommand,
)
from app.application.exceptions import AssetNotFoundError, PortfolioNotFoundError
from app.application.queries import (
    GetLatestMarketPriceQuery,
    GetPortfolioDashboardQuery,
    GetPortfolioQuery,
    ListAssetsQuery,
    ListPortfoliosQuery,
)
from app.application.results import (
    AssetPositionView,
    AssetView,
    MarketPriceView,
    PortfolioDashboard,
    PortfolioDetails,
    PortfolioSummary,
    TransactionView,
)
from app.application.unit_of_work import UnitOfWork
from app.domain.entities.asset import Asset
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.price_history import PriceHistory
from app.domain.entities.transaction import Transaction, TransactionType


class AssetApplicationService(ABC):
    """Define Asset use cases without implementation or persistence concerns."""

    @abstractmethod
    def create_asset(self, command: CreateAssetCommand) -> AssetView:
        """Create an Asset and return its descriptive view."""

    @abstractmethod
    def list_assets(self, query: ListAssetsQuery) -> tuple[AssetView, ...]:
        """Return a page of available Asset views."""


class DefaultAssetApplicationService(AssetApplicationService):
    """Orchestrate Asset use cases through an injected Unit of Work factory."""

    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def create_asset(self, command: CreateAssetCommand) -> AssetView:
        """Create and persist a new Asset."""
        with self._unit_of_work_factory() as unit_of_work:
            asset = Asset(
                symbol=command.symbol,
                name=command.name,
                asset_type=command.asset_type,
                currency=command.currency,
            )
            persisted = unit_of_work.assets.add(asset)
            unit_of_work.commit()
            return _to_asset_view(persisted)

    def list_assets(self, query: ListAssetsQuery) -> tuple[AssetView, ...]:
        """Return the repository-defined Asset page without committing."""
        with self._unit_of_work_factory() as unit_of_work:
            return tuple(
                _to_asset_view(asset)
                for asset in unit_of_work.assets.list(
                    offset=query.offset,
                    limit=query.limit,
                )
            )


class MarketPriceApplicationService(ABC):
    """Define manual market-price use cases without implementation concerns."""

    @abstractmethod
    def record_market_price(self, command: RecordMarketPriceCommand) -> MarketPriceView:
        """Record a price for an existing Asset."""

    @abstractmethod
    def get_latest_market_price(
        self,
        query: GetLatestMarketPriceQuery,
    ) -> MarketPriceView | None:
        """Return the latest price, or None when no price exists.

        Deterministic selection ordering belongs to the PriceHistory repository.
        """


class DefaultMarketPriceApplicationService(MarketPriceApplicationService):
    """Orchestrate market-price use cases through an injected Unit of Work factory."""

    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def record_market_price(self, command: RecordMarketPriceCommand) -> MarketPriceView:
        """Record a new immutable market price for an existing Asset."""
        with self._unit_of_work_factory() as unit_of_work:
            asset = _require_asset(unit_of_work, command.asset_id)
            price_history = PriceHistory(
                asset_id=asset.id,
                price=command.price,
                currency=asset.currency,
                observed_at=command.observed_at,
            )
            persisted = unit_of_work.price_history.add(price_history)
            unit_of_work.commit()
            return _to_market_price_view(persisted)

    def get_latest_market_price(
        self,
        query: GetLatestMarketPriceQuery,
    ) -> MarketPriceView | None:
        """Return the latest price for an existing Asset without committing."""
        with self._unit_of_work_factory() as unit_of_work:
            asset = _require_asset(unit_of_work, query.asset_id)
            price_history = unit_of_work.price_history.get_latest_for_asset(asset.id)
            return _to_market_price_view(price_history) if price_history is not None else None


class PortfolioDashboardQueryService(ABC):
    """Define calculated Portfolio dashboard queries without orchestration."""

    @abstractmethod
    def get_dashboard(self, query: GetPortfolioDashboardQuery) -> PortfolioDashboard:
        """Return a complete dashboard without committing."""


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
                asset=asset,
                quantity=command.quantity,
                unit_price=command.unit_price,
                trade_datetime=command.trade_datetime,
                transaction_type=TransactionType.BUY,
                commission=command.commission,
                tax=command.tax,
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
                asset=asset,
                quantity=command.quantity,
                unit_price=command.unit_price,
                trade_datetime=command.trade_datetime,
                transaction_type=TransactionType.SELL,
                commission=command.commission,
                tax=command.tax,
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
    *,
    quantity: Decimal,
    unit_price: Decimal,
    trade_datetime: datetime,
    transaction_type: TransactionType,
    commission: Decimal,
    tax: Decimal,
) -> Transaction:
    return Transaction(
        asset_id=asset.id,
        quantity=quantity,
        price=unit_price,
        transaction_type=transaction_type,
        commission=commission,
        tax=tax,
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


def _to_asset_view(asset: Asset) -> AssetView:
    return AssetView(
        id=asset.id,
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type,
        currency=asset.currency,
        is_active=asset.is_active,
        created_at=asset.created_at,
    )


def _to_market_price_view(price_history: PriceHistory) -> MarketPriceView:
    return MarketPriceView(
        id=price_history.id,
        asset_id=price_history.asset_id,
        price=price_history.price,
        currency=price_history.currency,
        observed_at=price_history.observed_at,
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


__all__ = [
    "AssetApplicationService",
    "DefaultAssetApplicationService",
    "DefaultMarketPriceApplicationService",
    "DefaultPortfolioApplicationService",
    "MarketPriceApplicationService",
    "PortfolioApplicationService",
    "PortfolioDashboardQueryService",
]
