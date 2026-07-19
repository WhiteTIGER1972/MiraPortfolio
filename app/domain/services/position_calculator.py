"""Pure moving-weighted-average portfolio position calculations."""

from decimal import Decimal
from uuid import UUID

from app.domain.entities.portfolio import Portfolio
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.exceptions import (
    InsufficientPositionError,
    UnsupportedTransactionTypeError,
)
from app.domain.value_objects.asset_position import AssetPosition

_ZERO = Decimal("0")


class PortfolioPositionCalculator:
    """Calculate transaction-derived positions in aggregate transaction order."""

    def calculate(self, portfolio: Portfolio) -> tuple[AssetPosition, ...]:
        """Return one immutable position for each transacted asset."""
        positions: dict[UUID, AssetPosition] = {}
        asset_order: list[UUID] = []

        for transaction in portfolio.transactions:
            current = positions.get(transaction.asset_id)
            if current is None:
                current = AssetPosition(
                    asset_id=transaction.asset_id,
                    quantity=_ZERO,
                    average_cost=_ZERO,
                    cost_basis=_ZERO,
                    realized_pnl=_ZERO,
                )
                asset_order.append(transaction.asset_id)

            if transaction.transaction_type is TransactionType.BUY:
                positions[transaction.asset_id] = self._apply_buy(current, transaction)
            elif transaction.transaction_type is TransactionType.SELL:
                positions[transaction.asset_id] = self._apply_sell(current, transaction)
            else:
                raise UnsupportedTransactionTypeError(
                    transaction.id,
                    transaction.transaction_type,
                )

        return tuple(positions[asset_id] for asset_id in asset_order)

    @staticmethod
    def _apply_buy(
        current: AssetPosition,
        transaction: Transaction,
    ) -> AssetPosition:
        gross_cost = transaction.quantity * transaction.price
        acquisition_cost = gross_cost + transaction.commission + transaction.tax
        new_quantity = current.quantity + transaction.quantity
        new_cost_basis = current.cost_basis + acquisition_cost
        new_average_cost = new_cost_basis / new_quantity
        return AssetPosition(
            asset_id=transaction.asset_id,
            quantity=new_quantity,
            average_cost=new_average_cost,
            cost_basis=new_cost_basis,
            realized_pnl=current.realized_pnl,
        )

    @staticmethod
    def _apply_sell(
        current: AssetPosition,
        transaction: Transaction,
    ) -> AssetPosition:
        if transaction.quantity > current.quantity:
            raise InsufficientPositionError(
                asset_id=transaction.asset_id,
                available_quantity=current.quantity,
                requested_quantity=transaction.quantity,
            )

        gross_proceeds = transaction.quantity * transaction.price
        net_proceeds = gross_proceeds - transaction.commission - transaction.tax
        disposed_cost = transaction.quantity * current.average_cost
        realized_change = net_proceeds - disposed_cost
        new_quantity = current.quantity - transaction.quantity
        new_realized_pnl = current.realized_pnl + realized_change

        if new_quantity == _ZERO:
            return AssetPosition(
                asset_id=transaction.asset_id,
                quantity=_ZERO,
                average_cost=_ZERO,
                cost_basis=_ZERO,
                realized_pnl=new_realized_pnl,
            )

        return AssetPosition(
            asset_id=transaction.asset_id,
            quantity=new_quantity,
            average_cost=current.average_cost,
            cost_basis=current.cost_basis - disposed_cost,
            realized_pnl=new_realized_pnl,
        )


__all__ = ["PortfolioPositionCalculator"]
