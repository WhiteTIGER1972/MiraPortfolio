"""Abstract portfolio application service contracts."""

from abc import ABC, abstractmethod

from app.application.commands import (
    BuyAssetCommand,
    CreatePortfolioCommand,
    DeleteTransactionCommand,
    SellAssetCommand,
)
from app.application.queries import GetPortfolioQuery, ListPortfoliosQuery
from app.application.results import PortfolioDetails, PortfolioSummary, TransactionView


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


__all__ = ["PortfolioApplicationService"]
