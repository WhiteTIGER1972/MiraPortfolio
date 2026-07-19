"""Contract tests for immutable application command DTOs."""

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.application.commands import BuyAssetCommand, SellAssetCommand

PORTFOLIO_ID = UUID("52b59f1d-878e-4ea1-a1a8-9d443be3243a")
ASSET_ID = UUID("09e8012d-9565-469a-ac83-492e5ff6deef")
QUANTITY = Decimal("12.345")
UNIT_PRICE = Decimal("987.654321")
TRADE_DATETIME = datetime(2026, 7, 19, 14, 30, 45, 123456, tzinfo=UTC)
TRANSACTION_COMMAND_FIELDS = (
    "portfolio_id",
    "asset_id",
    "quantity",
    "unit_price",
    "trade_datetime",
)


def test_buy_asset_command_preserves_exact_typed_values_and_is_immutable() -> None:
    command = BuyAssetCommand(
        portfolio_id=PORTFOLIO_ID,
        asset_id=ASSET_ID,
        quantity=QUANTITY,
        unit_price=UNIT_PRICE,
        trade_datetime=TRADE_DATETIME,
    )

    assert tuple(field.name for field in fields(BuyAssetCommand)) == TRANSACTION_COMMAND_FIELDS
    assert command.portfolio_id is PORTFOLIO_ID
    assert command.asset_id is ASSET_ID
    assert command.quantity is QUANTITY
    assert command.unit_price is UNIT_PRICE
    assert command.trade_datetime is TRADE_DATETIME
    assert not hasattr(command, "symbol")
    with pytest.raises(FrozenInstanceError):
        setattr(command, "asset_id", PORTFOLIO_ID)


def test_sell_asset_command_preserves_exact_typed_values_and_is_immutable() -> None:
    command = SellAssetCommand(
        portfolio_id=PORTFOLIO_ID,
        asset_id=ASSET_ID,
        quantity=QUANTITY,
        unit_price=UNIT_PRICE,
        trade_datetime=TRADE_DATETIME,
    )

    assert tuple(field.name for field in fields(SellAssetCommand)) == TRANSACTION_COMMAND_FIELDS
    assert command.portfolio_id is PORTFOLIO_ID
    assert command.asset_id is ASSET_ID
    assert command.quantity is QUANTITY
    assert command.unit_price is UNIT_PRICE
    assert command.trade_datetime is TRADE_DATETIME
    assert not hasattr(command, "symbol")
    with pytest.raises(FrozenInstanceError):
        setattr(command, "asset_id", PORTFOLIO_ID)
