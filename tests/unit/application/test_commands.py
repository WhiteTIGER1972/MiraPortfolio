"""Contract tests for immutable application command DTOs."""

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.application.commands import (
    BuyAssetCommand,
    CreateAssetCommand,
    RecordMarketPriceCommand,
    SellAssetCommand,
)
from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency

PORTFOLIO_ID = UUID("52b59f1d-878e-4ea1-a1a8-9d443be3243a")
ASSET_ID = UUID("09e8012d-9565-469a-ac83-492e5ff6deef")
QUANTITY = Decimal("12.345")
UNIT_PRICE = Decimal("987.654321")
COMMISSION = Decimal("1.2345")
TAX = Decimal("0.6789")
TRADE_DATETIME = datetime(2026, 7, 19, 14, 30, 45, 123456, tzinfo=UTC)
PRICE_OBSERVED_AT = datetime(2026, 7, 20, 10, 15, 30, 654321, tzinfo=UTC)
TRANSACTION_COMMAND_FIELDS = (
    "portfolio_id",
    "asset_id",
    "quantity",
    "unit_price",
    "trade_datetime",
    "commission",
    "tax",
)


def test_create_asset_command_has_exact_passive_immutable_contract() -> None:
    command = CreateAssetCommand(
        symbol="  bist  ",
        name="  Growth Asset  ",
        asset_type=AssetType.EQUITY,
        currency=Currency.TRY,
    )

    assert tuple(field.name for field in fields(CreateAssetCommand)) == (
        "symbol",
        "name",
        "asset_type",
        "currency",
    )
    assert command.symbol == "  bist  "
    assert command.name == "  Growth Asset  "
    assert command.asset_type is AssetType.EQUITY
    assert command.currency is Currency.TRY
    assert not hasattr(command, "__dict__")
    assert not {
        "id",
        "is_active",
        "created_at",
        "provider",
        "exchange",
        "market",
        "isin",
        "price",
        "portfolio_id",
    } & {field.name for field in fields(CreateAssetCommand)}
    with pytest.raises(FrozenInstanceError):
        setattr(command, "symbol", "CHANGED")


def test_record_market_price_command_has_exact_passive_immutable_contract() -> None:
    zero_price = Decimal("0")
    command = RecordMarketPriceCommand(
        asset_id=ASSET_ID,
        price=zero_price,
        observed_at=PRICE_OBSERVED_AT,
    )

    assert tuple(field.name for field in fields(RecordMarketPriceCommand)) == (
        "asset_id",
        "price",
        "observed_at",
    )
    assert command.asset_id is ASSET_ID
    assert command.price is zero_price
    assert command.observed_at is PRICE_OBSERVED_AT
    assert not hasattr(command, "__dict__")
    assert not {
        "symbol",
        "name",
        "currency",
        "portfolio_id",
        "id",
        "provider",
        "source",
        "is_stale",
        "is_fresh",
    } & {field.name for field in fields(RecordMarketPriceCommand)}
    with pytest.raises(FrozenInstanceError):
        setattr(command, "price", Decimal("1"))


def test_buy_asset_command_preserves_exact_typed_values_and_is_immutable() -> None:
    command = BuyAssetCommand(
        portfolio_id=PORTFOLIO_ID,
        asset_id=ASSET_ID,
        quantity=QUANTITY,
        unit_price=UNIT_PRICE,
        trade_datetime=TRADE_DATETIME,
        commission=COMMISSION,
        tax=TAX,
    )

    assert tuple(field.name for field in fields(BuyAssetCommand)) == TRANSACTION_COMMAND_FIELDS
    assert command.portfolio_id is PORTFOLIO_ID
    assert command.asset_id is ASSET_ID
    assert command.quantity is QUANTITY
    assert command.unit_price is UNIT_PRICE
    assert command.trade_datetime is TRADE_DATETIME
    assert command.commission is COMMISSION
    assert command.tax is TAX
    assert not hasattr(command, "symbol")
    assert not hasattr(command, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(command, "asset_id", PORTFOLIO_ID)


def test_sell_asset_command_preserves_exact_typed_values_and_is_immutable() -> None:
    command = SellAssetCommand(
        portfolio_id=PORTFOLIO_ID,
        asset_id=ASSET_ID,
        quantity=QUANTITY,
        unit_price=UNIT_PRICE,
        trade_datetime=TRADE_DATETIME,
        commission=COMMISSION,
        tax=TAX,
    )

    assert tuple(field.name for field in fields(SellAssetCommand)) == TRANSACTION_COMMAND_FIELDS
    assert command.portfolio_id is PORTFOLIO_ID
    assert command.asset_id is ASSET_ID
    assert command.quantity is QUANTITY
    assert command.unit_price is UNIT_PRICE
    assert command.trade_datetime is TRADE_DATETIME
    assert command.commission is COMMISSION
    assert command.tax is TAX
    assert not hasattr(command, "symbol")
    assert not hasattr(command, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(command, "asset_id", PORTFOLIO_ID)


def test_transaction_command_charge_defaults_preserve_existing_callers() -> None:
    buy = BuyAssetCommand(
        portfolio_id=PORTFOLIO_ID,
        asset_id=ASSET_ID,
        quantity=QUANTITY,
        unit_price=UNIT_PRICE,
        trade_datetime=TRADE_DATETIME,
    )
    sell = SellAssetCommand(
        portfolio_id=PORTFOLIO_ID,
        asset_id=ASSET_ID,
        quantity=QUANTITY,
        unit_price=UNIT_PRICE,
        trade_datetime=TRADE_DATETIME,
    )

    assert buy.commission == Decimal("0")
    assert buy.tax == Decimal("0")
    assert sell.commission == Decimal("0")
    assert sell.tax == Decimal("0")
