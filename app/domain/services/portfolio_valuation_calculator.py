"""Pure portfolio valuation from positions and caller-supplied prices."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.domain.entities.asset import Asset
from app.domain.entities.portfolio import Portfolio
from app.domain.exceptions import (
    InvalidMarketPriceError,
    MissingMarketPriceError,
    PositionAssetNotFoundError,
)
from app.domain.services.position_calculator import PortfolioPositionCalculator
from app.domain.value_objects.asset_position import AssetPosition
from app.domain.value_objects.currency import Currency
from app.domain.value_objects.portfolio_valuation import (
    CurrencyValuation,
    PortfolioValuation,
    ValuedAssetPosition,
)

_ZERO = Decimal("0")


class _PositionCalculator(Protocol):
    def calculate(self, portfolio: Portfolio) -> tuple[AssetPosition, ...]:
        """Return transaction-derived positions in canonical order."""


class PortfolioValuationCalculator:
    """Value transaction-derived positions without loading prices or converting currencies."""

    def __init__(
        self,
        position_calculator: _PositionCalculator | None = None,
    ) -> None:
        self._position_calculator = (
            position_calculator
            if position_calculator is not None
            else PortfolioPositionCalculator()
        )

    def calculate(
        self,
        portfolio: Portfolio,
        market_prices: Mapping[UUID, Decimal],
    ) -> PortfolioValuation:
        """Return ordered per-position and per-currency valuation results."""
        positions = self._position_calculator.calculate(portfolio)
        assets_by_id = {asset.id: asset for asset in portfolio.assets}
        valued_positions = tuple(
            self._value_position(position, assets_by_id, market_prices) for position in positions
        )
        return PortfolioValuation(
            positions=valued_positions,
            currencies=self._summarize_currencies(valued_positions),
        )

    @staticmethod
    def _value_position(
        position: AssetPosition,
        assets_by_id: Mapping[UUID, Asset],
        market_prices: Mapping[UUID, Decimal],
    ) -> ValuedAssetPosition:
        asset = assets_by_id.get(position.asset_id)
        if asset is None:
            raise PositionAssetNotFoundError(position.asset_id)

        if position.quantity == _ZERO:
            return ValuedAssetPosition(
                asset_id=position.asset_id,
                currency=asset.currency,
                quantity=position.quantity,
                average_cost=position.average_cost,
                cost_basis=position.cost_basis,
                market_price=None,
                market_value=_ZERO,
                realized_pnl=position.realized_pnl,
                unrealized_pnl=_ZERO,
                total_pnl=position.realized_pnl,
            )

        try:
            market_price = market_prices[position.asset_id]
        except KeyError:
            raise MissingMarketPriceError(position.asset_id) from None
        if not isinstance(market_price, Decimal):
            raise TypeError(f"Market price for Asset {position.asset_id} must be a Decimal.")
        if market_price < _ZERO:
            raise InvalidMarketPriceError(position.asset_id, market_price)

        market_value = position.quantity * market_price
        unrealized_pnl = market_value - position.cost_basis
        return ValuedAssetPosition(
            asset_id=position.asset_id,
            currency=asset.currency,
            quantity=position.quantity,
            average_cost=position.average_cost,
            cost_basis=position.cost_basis,
            market_price=market_price,
            market_value=market_value,
            realized_pnl=position.realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_pnl=position.realized_pnl + unrealized_pnl,
        )

    @staticmethod
    def _summarize_currencies(
        positions: tuple[ValuedAssetPosition, ...],
    ) -> tuple[CurrencyValuation, ...]:
        valuations: dict[Currency, CurrencyValuation] = {}
        currency_order: list[Currency] = []
        for position in positions:
            current = valuations.get(position.currency)
            if current is None:
                current = CurrencyValuation(
                    currency=position.currency,
                    cost_basis=_ZERO,
                    market_value=_ZERO,
                    realized_pnl=_ZERO,
                    unrealized_pnl=_ZERO,
                    total_pnl=_ZERO,
                )
                currency_order.append(position.currency)
            realized_pnl = current.realized_pnl + position.realized_pnl
            unrealized_pnl = current.unrealized_pnl + position.unrealized_pnl
            valuations[position.currency] = CurrencyValuation(
                currency=position.currency,
                cost_basis=current.cost_basis + position.cost_basis,
                market_value=current.market_value + position.market_value,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                total_pnl=realized_pnl + unrealized_pnl,
            )
        return tuple(valuations[currency] for currency in currency_order)


__all__ = ["PortfolioValuationCalculator"]
