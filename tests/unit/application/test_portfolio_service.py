"""Focused tests for framework-independent portfolio application workflows."""

import ast
import inspect
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Literal, Self
from uuid import UUID

import pytest

from app.application.commands import (
    BuyAssetCommand,
    CreatePortfolioCommand,
    DeleteTransactionCommand,
    SellAssetCommand,
)
from app.application.exceptions import AssetNotFoundError, PortfolioNotFoundError
from app.application.ports import PriceHistoryRepository, SnapshotRepository
from app.application.queries import GetPortfolioQuery, ListPortfoliosQuery
from app.application.results import (
    AssetPositionView,
    PortfolioDetails,
    PortfolioSummary,
    TransactionView,
)
from app.application.services import DefaultPortfolioApplicationService
from app.core.exceptions import RepositoryError
from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.transaction import Transaction, TransactionType
from app.domain.exceptions import TransactionNotFoundError
from app.domain.value_objects.currency import Currency

NOW = datetime(2026, 7, 19, 14, 30, 45, 123456, tzinfo=UTC)
LATER = datetime(2026, 7, 19, 16, 45, 12, 654321, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICE_MODULE = PROJECT_ROOT / "app/application/services.py"


class FakeAssetRepository:
    """Record Asset repository interactions while enforcing active UoW access."""

    def __init__(
        self,
        items: dict[UUID, Asset],
        is_active: Callable[[], bool],
    ) -> None:
        self.items = items
        self._is_active = is_active
        self.added: list[Asset] = []
        self.get_calls: list[UUID] = []
        self.list_calls: list[tuple[int, int]] = []
        self.deleted: list[UUID] = []

    def _require_active(self) -> None:
        if not self._is_active():
            raise RuntimeError("Repository accessed outside its Unit of Work.")

    def add(self, asset: Asset) -> Asset:
        self._require_active()
        self.added.append(asset)
        self.items[asset.id] = asset
        return asset

    def get(self, asset_id: UUID) -> Asset | None:
        self._require_active()
        self.get_calls.append(asset_id)
        return self.items.get(asset_id)

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Asset]:
        self._require_active()
        self.list_calls.append((offset, limit))
        return list(self.items.values())[offset : offset + limit]

    def delete(self, asset_id: UUID) -> None:
        self._require_active()
        self.deleted.append(asset_id)
        self.items.pop(asset_id, None)

    def exists(self, asset_id: UUID) -> bool:
        self._require_active()
        return asset_id in self.items

    def count(self) -> int:
        self._require_active()
        return len(self.items)


class FakePortfolioRepository:
    """Record Portfolio repository interactions while enforcing active UoW access."""

    def __init__(
        self,
        items: dict[UUID, Portfolio],
        is_active: Callable[[], bool],
        save_error: BaseException | None,
    ) -> None:
        self.items = items
        self._is_active = is_active
        self._save_error = save_error
        self.added: list[Portfolio] = []
        self.saved: list[Portfolio] = []
        self.get_calls: list[UUID] = []
        self.list_calls: list[tuple[int, int]] = []
        self.deleted: list[UUID] = []

    def _require_active(self) -> None:
        if not self._is_active():
            raise RuntimeError("Repository accessed outside its Unit of Work.")

    def add(self, portfolio: Portfolio) -> Portfolio:
        self._require_active()
        self.added.append(portfolio)
        self.items[portfolio.id] = portfolio
        return portfolio

    def save(self, portfolio: Portfolio) -> Portfolio:
        self._require_active()
        self.saved.append(portfolio)
        if self._save_error is not None:
            raise self._save_error
        self.items[portfolio.id] = portfolio
        return portfolio

    def get(self, portfolio_id: UUID) -> Portfolio | None:
        self._require_active()
        self.get_calls.append(portfolio_id)
        return self.items.get(portfolio_id)

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Portfolio]:
        self._require_active()
        self.list_calls.append((offset, limit))
        return list(self.items.values())[offset : offset + limit]

    def delete(self, portfolio_id: UUID) -> None:
        self._require_active()
        self.deleted.append(portfolio_id)
        self.items.pop(portfolio_id, None)

    def exists(self, portfolio_id: UUID) -> bool:
        self._require_active()
        return portfolio_id in self.items

    def count(self) -> int:
        self._require_active()
        return len(self.items)


class TrackingUnitOfWork:
    """Expose active-only fake repositories and record lifecycle operations."""

    def __init__(
        self,
        assets: dict[UUID, Asset],
        portfolios: dict[UUID, Portfolio],
        save_error: BaseException | None,
    ) -> None:
        self.active = False
        self.enter_count = 0
        self.exit_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.exit_exceptions: list[type[BaseException] | None] = []
        self.asset_repository = FakeAssetRepository(assets, lambda: self.active)
        self.portfolio_repository = FakePortfolioRepository(
            portfolios,
            lambda: self.active,
            save_error,
        )

    @property
    def assets(self) -> FakeAssetRepository:
        self._require_active()
        return self.asset_repository

    @property
    def portfolios(self) -> FakePortfolioRepository:
        self._require_active()
        return self.portfolio_repository

    @property
    def price_history(self) -> PriceHistoryRepository:
        self._require_active()
        raise AssertionError("Price history repository is outside the tested workflow.")

    @property
    def snapshots(self) -> SnapshotRepository:
        self._require_active()
        raise AssertionError("Snapshot repository is outside the tested workflow.")

    def __enter__(self) -> Self:
        if self.active:
            raise RuntimeError("Unit of Work is already active.")
        self.active = True
        self.enter_count += 1
        return self

    def commit(self) -> None:
        self._require_active()
        self.commit_count += 1

    def rollback(self) -> None:
        self._require_active()
        self.rollback_count += 1

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exception, traceback
        self._require_active()
        self.exit_count += 1
        self.exit_exceptions.append(exception_type)
        self.active = False
        return False

    def _require_active(self) -> None:
        if not self.active:
            raise RuntimeError("Unit of Work is not active.")


class TrackingUnitOfWorkFactory:
    """Create a fresh tracking Unit of Work for every invocation."""

    def __init__(
        self,
        *,
        assets: Sequence[Asset] = (),
        portfolios: Sequence[Portfolio] = (),
        save_error: BaseException | None = None,
    ) -> None:
        self.asset_state = {asset.id: asset for asset in assets}
        self.portfolio_state = {portfolio.id: portfolio for portfolio in portfolios}
        self.save_error = save_error
        self.created: list[TrackingUnitOfWork] = []

    def __call__(self) -> TrackingUnitOfWork:
        unit_of_work = TrackingUnitOfWork(
            self.asset_state,
            self.portfolio_state,
            self.save_error,
        )
        self.created.append(unit_of_work)
        return unit_of_work


def make_asset(
    *,
    identity: int,
    symbol: str,
    currency: Currency = Currency.TRY,
) -> Asset:
    return Asset(
        id=UUID(int=identity),
        symbol=symbol,
        name=f"{symbol} Asset",
        asset_type=AssetType.EQUITY,
        currency=currency,
        created_at=NOW,
    )


def make_portfolio(
    *,
    identity: int,
    name: str,
    assets: Sequence[Asset] = (),
) -> Portfolio:
    portfolio = Portfolio(
        id=UUID(int=identity),
        name=name,
        base_currency=Currency.USD,
        created_at=NOW,
    )
    for asset in assets:
        portfolio.add_asset(asset)
    return portfolio


def make_transaction(
    *,
    identity: int,
    asset_id: UUID,
    quantity: str,
    price: str,
    transaction_type: TransactionType,
    date: datetime,
) -> Transaction:
    return Transaction(
        id=UUID(int=identity),
        asset_id=asset_id,
        quantity=Decimal(quantity),
        price=Decimal(price),
        transaction_type=transaction_type,
        date=date,
    )


def only_unit_of_work(factory: TrackingUnitOfWorkFactory) -> TrackingUnitOfWork:
    assert len(factory.created) == 1
    return factory.created[0]


def test_create_portfolio_adds_new_aggregate_and_commits_once() -> None:
    factory = TrackingUnitOfWorkFactory()
    service = DefaultPortfolioApplicationService(factory)

    result = service.create_portfolio(CreatePortfolioCommand(portfolio_name="  Long Term  "))

    unit_of_work = only_unit_of_work(factory)
    repository = unit_of_work.portfolio_repository
    assert len(repository.added) == 1
    portfolio = repository.added[0]
    assert portfolio.name == "Long Term"
    assert repository.saved == []
    assert result == PortfolioDetails(
        id=portfolio.id,
        name="Long Term",
        base_currency=Currency.TRY,
        assets=(),
        transactions=(),
        is_archived=False,
        created_at=portfolio.created_at,
    )
    assert unit_of_work.commit_count == 1
    assert unit_of_work.enter_count == unit_of_work.exit_count == 1
    assert unit_of_work.rollback_count == 0


def test_create_portfolio_domain_failure_does_not_add_or_commit() -> None:
    factory = TrackingUnitOfWorkFactory()
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(ValueError, match="cannot be empty"):
        service.create_portfolio(CreatePortfolioCommand(portfolio_name="   "))

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.portfolio_repository.added == []
    assert unit_of_work.commit_count == 0
    assert unit_of_work.exit_exceptions == [ValueError]


def test_buy_asset_records_buy_saves_and_commits_with_exact_values() -> None:
    asset = make_asset(identity=1, symbol="buy")
    portfolio = make_portfolio(identity=10, name="Buying")
    quantity = Decimal("12.345")
    unit_price = Decimal("987.654321")
    factory = TrackingUnitOfWorkFactory(assets=(asset,), portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    result = service.buy_asset(
        BuyAssetCommand(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            quantity=quantity,
            unit_price=unit_price,
            trade_datetime=LATER,
        )
    )

    unit_of_work = only_unit_of_work(factory)
    repository = unit_of_work.portfolio_repository
    assert repository.get_calls == [portfolio.id]
    assert unit_of_work.asset_repository.get_calls == [asset.id]
    assert repository.saved == [portfolio]
    assert portfolio.assets == [asset]
    assert len(portfolio.transactions) == 1
    transaction = portfolio.transactions[0]
    assert transaction.transaction_type is TransactionType.BUY
    assert result == TransactionView(
        id=transaction.id,
        asset_id=asset.id,
        quantity=quantity,
        price=unit_price,
        transaction_type=TransactionType.BUY,
        commission=Decimal("0"),
        tax=Decimal("0"),
        date=LATER,
    )
    assert result.quantity is quantity
    assert result.price is unit_price
    assert result.date is LATER
    assert unit_of_work.commit_count == 1


def test_buy_asset_propagates_exact_commission_and_tax() -> None:
    asset = make_asset(identity=1, symbol="FEES")
    portfolio = make_portfolio(identity=10, name="Buy Charges")
    commission = Decimal("1.234500")
    tax = Decimal("0.678900")
    factory = TrackingUnitOfWorkFactory(assets=(asset,), portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    result = service.buy_asset(
        BuyAssetCommand(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            quantity=Decimal("2.5"),
            unit_price=Decimal("40.25"),
            trade_datetime=LATER,
            commission=commission,
            tax=tax,
        )
    )

    unit_of_work = only_unit_of_work(factory)
    transaction = portfolio.transactions[0]
    assert transaction.commission is commission
    assert transaction.tax is tax
    assert result.commission is commission
    assert result.tax is tax
    assert unit_of_work.portfolio_repository.saved == [portfolio]
    assert unit_of_work.commit_count == 1


def test_buy_negative_commission_preserves_domain_failure_without_save_or_commit() -> None:
    asset = make_asset(identity=1, symbol="INVALID")
    portfolio = make_portfolio(identity=10, name="Invalid Buy Charge")
    factory = TrackingUnitOfWorkFactory(assets=(asset,), portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(ValueError, match="commission cannot be negative"):
        service.buy_asset(
            BuyAssetCommand(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                quantity=Decimal("1"),
                unit_price=Decimal("2"),
                trade_datetime=NOW,
                commission=Decimal("-0.01"),
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert portfolio.assets == []
    assert portfolio.transactions == []
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_buy_missing_portfolio_translates_absence_without_asset_lookup_or_commit() -> None:
    asset = make_asset(identity=1, symbol="BUY")
    missing_portfolio_id = UUID(int=999)
    factory = TrackingUnitOfWorkFactory(assets=(asset,))
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(PortfolioNotFoundError, match=str(missing_portfolio_id)):
        service.buy_asset(
            BuyAssetCommand(
                portfolio_id=missing_portfolio_id,
                asset_id=asset.id,
                quantity=Decimal("1"),
                unit_price=Decimal("2"),
                trade_datetime=NOW,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.asset_repository.get_calls == []
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_buy_missing_asset_translates_absence_without_save_or_commit() -> None:
    portfolio = make_portfolio(identity=10, name="Missing Asset")
    missing_asset_id = UUID(int=999)
    factory = TrackingUnitOfWorkFactory(portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(AssetNotFoundError, match=str(missing_asset_id)):
        service.buy_asset(
            BuyAssetCommand(
                portfolio_id=portfolio.id,
                asset_id=missing_asset_id,
                quantity=Decimal("1"),
                unit_price=Decimal("2"),
                trade_datetime=NOW,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_buy_domain_failure_does_not_mutate_save_or_commit() -> None:
    asset = make_asset(identity=1, symbol="INVALID")
    portfolio = make_portfolio(identity=10, name="Invalid Buy")
    factory = TrackingUnitOfWorkFactory(assets=(asset,), portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(ValueError, match="quantity must be positive"):
        service.buy_asset(
            BuyAssetCommand(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                quantity=Decimal("0"),
                unit_price=Decimal("2"),
                trade_datetime=NOW,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert portfolio.assets == []
    assert portfolio.transactions == []
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_buy_save_failure_propagates_without_commit() -> None:
    asset = make_asset(identity=1, symbol="FAIL")
    portfolio = make_portfolio(identity=10, name="Save Failure")
    failure = RepositoryError("save failed")
    factory = TrackingUnitOfWorkFactory(
        assets=(asset,),
        portfolios=(portfolio,),
        save_error=failure,
    )
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(RepositoryError) as raised:
        service.buy_asset(
            BuyAssetCommand(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                quantity=Decimal("1"),
                unit_price=Decimal("2"),
                trade_datetime=NOW,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert raised.value is failure
    assert unit_of_work.portfolio_repository.saved == [portfolio]
    assert unit_of_work.commit_count == 0
    assert unit_of_work.exit_exceptions == [RepositoryError]


def test_sell_asset_records_sell_saves_and_commits_once() -> None:
    asset = make_asset(identity=1, symbol="SELL")
    portfolio = make_portfolio(identity=10, name="Selling", assets=(asset,))
    quantity = Decimal("3.75")
    unit_price = Decimal("45.125")
    factory = TrackingUnitOfWorkFactory(assets=(asset,), portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    result = service.sell_asset(
        SellAssetCommand(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            quantity=quantity,
            unit_price=unit_price,
            trade_datetime=LATER,
        )
    )

    unit_of_work = only_unit_of_work(factory)
    transaction = portfolio.transactions[0]
    assert transaction.transaction_type is TransactionType.SELL
    assert result.id == transaction.id
    assert result.asset_id == asset.id
    assert result.quantity is quantity
    assert result.price is unit_price
    assert result.commission == Decimal("0")
    assert result.tax == Decimal("0")
    assert result.date is LATER
    assert unit_of_work.portfolio_repository.saved == [portfolio]
    assert unit_of_work.commit_count == 1


def test_sell_asset_propagates_exact_commission_and_tax() -> None:
    asset = make_asset(identity=1, symbol="FEES")
    portfolio = make_portfolio(identity=10, name="Sell Charges", assets=(asset,))
    commission = Decimal("2.345600")
    tax = Decimal("0.789100")
    factory = TrackingUnitOfWorkFactory(assets=(asset,), portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    result = service.sell_asset(
        SellAssetCommand(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            quantity=Decimal("1.5"),
            unit_price=Decimal("75.125"),
            trade_datetime=LATER,
            commission=commission,
            tax=tax,
        )
    )

    unit_of_work = only_unit_of_work(factory)
    transaction = portfolio.transactions[0]
    assert transaction.commission is commission
    assert transaction.tax is tax
    assert result.commission is commission
    assert result.tax is tax
    assert unit_of_work.portfolio_repository.saved == [portfolio]
    assert unit_of_work.commit_count == 1


def test_sell_negative_tax_preserves_domain_failure_without_save_or_commit() -> None:
    asset = make_asset(identity=1, symbol="INVALID")
    portfolio = make_portfolio(identity=10, name="Invalid Sell Charge", assets=(asset,))
    factory = TrackingUnitOfWorkFactory(assets=(asset,), portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(ValueError, match="tax cannot be negative"):
        service.sell_asset(
            SellAssetCommand(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                quantity=Decimal("1"),
                unit_price=Decimal("2"),
                trade_datetime=NOW,
                tax=Decimal("-0.01"),
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert portfolio.assets == [asset]
    assert portfolio.transactions == []
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_sell_missing_portfolio_does_not_lookup_asset_save_or_commit() -> None:
    asset = make_asset(identity=1, symbol="SELL")
    missing_portfolio_id = UUID(int=999)
    factory = TrackingUnitOfWorkFactory(assets=(asset,))
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(PortfolioNotFoundError):
        service.sell_asset(
            SellAssetCommand(
                portfolio_id=missing_portfolio_id,
                asset_id=asset.id,
                quantity=Decimal("1"),
                unit_price=Decimal("2"),
                trade_datetime=NOW,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.asset_repository.get_calls == []
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_sell_missing_asset_does_not_save_or_commit() -> None:
    portfolio = make_portfolio(identity=10, name="Missing Asset")
    missing_asset_id = UUID(int=999)
    factory = TrackingUnitOfWorkFactory(portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(AssetNotFoundError):
        service.sell_asset(
            SellAssetCommand(
                portfolio_id=portfolio.id,
                asset_id=missing_asset_id,
                quantity=Decimal("1"),
                unit_price=Decimal("2"),
                trade_datetime=NOW,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_sell_preserves_domain_membership_failure_without_commit() -> None:
    asset = make_asset(identity=1, symbol="OUTSIDE")
    portfolio = make_portfolio(identity=10, name="Not Held")
    factory = TrackingUnitOfWorkFactory(assets=(asset,), portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(ValueError, match="must belong"):
        service.sell_asset(
            SellAssetCommand(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                quantity=Decimal("1"),
                unit_price=Decimal("2"),
                trade_datetime=NOW,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_delete_transaction_uses_aggregate_behavior_saves_and_preserves_assets() -> None:
    asset = make_asset(identity=1, symbol="KEEP")
    portfolio = make_portfolio(identity=10, name="Delete", assets=(asset,))
    first = make_transaction(
        identity=101,
        asset_id=asset.id,
        quantity="1",
        price="10",
        transaction_type=TransactionType.BUY,
        date=NOW,
    )
    second = make_transaction(
        identity=102,
        asset_id=asset.id,
        quantity="2",
        price="20",
        transaction_type=TransactionType.SELL,
        date=LATER,
    )
    portfolio.record_transaction(first)
    portfolio.record_transaction(second)
    factory = TrackingUnitOfWorkFactory(assets=(asset,), portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    result = service.delete_transaction(
        DeleteTransactionCommand(
            portfolio_id=portfolio.id,
            transaction_id=first.id,
        )
    )

    unit_of_work = only_unit_of_work(factory)
    assert portfolio.transactions == [second]
    assert portfolio.assets == [asset]
    assert unit_of_work.portfolio_repository.saved == [portfolio]
    assert result.assets[0].id == asset.id
    assert tuple(view.id for view in result.transactions) == (second.id,)
    assert unit_of_work.commit_count == 1


def test_delete_transaction_missing_portfolio_does_not_save_or_commit() -> None:
    missing_portfolio_id = UUID(int=999)
    factory = TrackingUnitOfWorkFactory()
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(PortfolioNotFoundError):
        service.delete_transaction(
            DeleteTransactionCommand(
                portfolio_id=missing_portfolio_id,
                transaction_id=UUID(int=101),
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_delete_unknown_transaction_preserves_domain_error_without_commit() -> None:
    asset = make_asset(identity=1, symbol="KEEP")
    portfolio = make_portfolio(identity=10, name="Missing Transaction", assets=(asset,))
    transaction = make_transaction(
        identity=101,
        asset_id=asset.id,
        quantity="1",
        price="10",
        transaction_type=TransactionType.BUY,
        date=NOW,
    )
    portfolio.record_transaction(transaction)
    unknown_id = UUID(int=999)
    factory = TrackingUnitOfWorkFactory(assets=(asset,), portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(TransactionNotFoundError) as raised:
        service.delete_transaction(
            DeleteTransactionCommand(
                portfolio_id=portfolio.id,
                transaction_id=unknown_id,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert raised.value.transaction_id == unknown_id
    assert portfolio.transactions == [transaction]
    assert portfolio.assets == [asset]
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_get_portfolio_maps_public_state_and_preserves_nested_order_and_types() -> None:
    second_asset = make_asset(identity=2, symbol="SECOND", currency=Currency.USD)
    first_asset = make_asset(identity=1, symbol="FIRST")
    portfolio = make_portfolio(
        identity=10,
        name="Detailed",
        assets=(second_asset, first_asset),
    )
    first_transaction = make_transaction(
        identity=102,
        asset_id=second_asset.id,
        quantity="1.25",
        price="10.125",
        transaction_type=TransactionType.BUY,
        date=NOW,
    )
    second_transaction = make_transaction(
        identity=101,
        asset_id=first_asset.id,
        quantity="2.50",
        price="20.250",
        transaction_type=TransactionType.SELL,
        date=LATER,
    )
    portfolio.record_transaction(first_transaction)
    portfolio.record_transaction(second_transaction)
    factory = TrackingUnitOfWorkFactory(
        assets=(first_asset, second_asset),
        portfolios=(portfolio,),
    )
    service = DefaultPortfolioApplicationService(factory)

    result = service.get_portfolio(GetPortfolioQuery(portfolio_id=portfolio.id))

    unit_of_work = only_unit_of_work(factory)
    assert result.id is portfolio.id
    assert result.base_currency is Currency.USD
    assert result.assets == (
        AssetPositionView(
            id=second_asset.id,
            symbol=second_asset.symbol,
            name=second_asset.name,
            asset_type=second_asset.asset_type,
            currency=second_asset.currency,
            is_active=second_asset.is_active,
            created_at=second_asset.created_at,
        ),
        AssetPositionView(
            id=first_asset.id,
            symbol=first_asset.symbol,
            name=first_asset.name,
            asset_type=first_asset.asset_type,
            currency=first_asset.currency,
            is_active=first_asset.is_active,
            created_at=first_asset.created_at,
        ),
    )
    assert tuple(view.id for view in result.transactions) == (
        first_transaction.id,
        second_transaction.id,
    )
    assert result.transactions[0].quantity is first_transaction.quantity
    assert result.transactions[0].price is first_transaction.price
    assert result.transactions[0].date is first_transaction.date
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_get_missing_portfolio_translates_absence_without_commit() -> None:
    missing_id = UUID(int=999)
    factory = TrackingUnitOfWorkFactory()
    service = DefaultPortfolioApplicationService(factory)

    with pytest.raises(PortfolioNotFoundError, match=str(missing_id)):
        service.get_portfolio(GetPortfolioQuery(portfolio_id=missing_id))

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.commit_count == 0
    assert unit_of_work.exit_exceptions == [PortfolioNotFoundError]


def test_list_portfolios_preserves_repository_order_and_returns_tuple_without_commit() -> None:
    second = make_portfolio(identity=2, name="Second")
    first = make_portfolio(identity=1, name="First")
    factory = TrackingUnitOfWorkFactory(portfolios=(second, first))
    service = DefaultPortfolioApplicationService(factory)

    result = service.list_portfolios(ListPortfoliosQuery())

    unit_of_work = only_unit_of_work(factory)
    assert result == (
        PortfolioSummary(
            id=second.id,
            name=second.name,
            base_currency=second.base_currency,
            is_archived=second.is_archived,
            created_at=second.created_at,
        ),
        PortfolioSummary(
            id=first.id,
            name=first.name,
            base_currency=first.base_currency,
            is_archived=first.is_archived,
            created_at=first.created_at,
        ),
    )
    assert unit_of_work.portfolio_repository.list_calls == [(0, 100)]
    assert unit_of_work.portfolio_repository.saved == []
    assert unit_of_work.commit_count == 0


def test_each_service_call_uses_one_fresh_unit_of_work_context() -> None:
    portfolio = make_portfolio(identity=10, name="Lifecycle")
    factory = TrackingUnitOfWorkFactory(portfolios=(portfolio,))
    service = DefaultPortfolioApplicationService(factory)

    service.get_portfolio(GetPortfolioQuery(portfolio_id=portfolio.id))
    service.list_portfolios(ListPortfoliosQuery())

    assert len(factory.created) == 2
    assert factory.created[0] is not factory.created[1]
    for unit_of_work in factory.created:
        assert unit_of_work.enter_count == 1
        assert unit_of_work.exit_count == 1
        assert unit_of_work.commit_count == 0
        assert unit_of_work.rollback_count == 0
        assert not unit_of_work.active


def test_default_service_remains_framework_independent_and_has_explicit_commits() -> None:
    source = SERVICE_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SERVICE_MODULE))
    imported_modules = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden_imports = {
        "sqlalchemy",
        "sqlite3",
        "PySide6",
        "app.infrastructure",
        "app.ui",
    }

    assert not {
        imported
        for imported in imported_modules
        if any(
            imported == forbidden or imported.startswith(f"{forbidden}.")
            for forbidden in forbidden_imports
        )
    }
    portfolio_service_source = inspect.getsource(DefaultPortfolioApplicationService)
    assert "get_by_symbol" not in portfolio_service_source
    assert "command.symbol" not in portfolio_service_source
    assert "._transactions" not in portfolio_service_source
    assert ".rollback(" not in portfolio_service_source
    assert "float(" not in portfolio_service_source
    assert portfolio_service_source.count("unit_of_work.commit()") == 4
