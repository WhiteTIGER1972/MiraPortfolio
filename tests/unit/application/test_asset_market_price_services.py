"""Focused tests for Asset and manual market-price application workflows."""

import ast
import inspect
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Literal, Self, get_type_hints
from uuid import UUID

import pytest

import app.application as application
from app.application.commands import CreateAssetCommand, RecordMarketPriceCommand
from app.application.exceptions import AssetNotFoundError
from app.application.ports import PortfolioRepository, SnapshotRepository
from app.application.queries import GetLatestMarketPriceQuery, ListAssetsQuery
from app.application.results import AssetView, MarketPriceView
from app.application.services import (
    AssetApplicationService,
    DefaultAssetApplicationService,
    DefaultMarketPriceApplicationService,
    MarketPriceApplicationService,
)
from app.application.unit_of_work import UnitOfWork
from app.core.exceptions import RepositoryError
from app.domain.entities.asset import Asset, AssetType
from app.domain.entities.price_history import PriceHistory
from app.domain.value_objects.currency import Currency

NOW = datetime(2026, 7, 20, 15, 45, 12, 654321, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICES_MODULE = PROJECT_ROOT / "app/application/services.py"


class FakeAssetRepository:
    """Record Asset repository calls while enforcing active UnitOfWork access."""

    def __init__(
        self,
        items: list[Asset],
        is_active: Callable[[], bool],
        events: list[str],
        *,
        add_error: BaseException | None,
        list_error: BaseException | None,
    ) -> None:
        self.items = items
        self._is_active = is_active
        self._events = events
        self._add_error = add_error
        self._list_error = list_error
        self.added: list[Asset] = []
        self.get_calls: list[UUID] = []
        self.list_calls: list[tuple[int, int]] = []
        self.delete_calls: list[UUID] = []
        self.exists_calls: list[UUID] = []
        self.count_calls = 0

    def _require_active(self) -> None:
        if not self._is_active():
            raise RuntimeError("Repository accessed outside its Unit of Work.")

    def add(self, asset: Asset) -> Asset:
        self._require_active()
        self._events.append("asset.add")
        self.added.append(asset)
        if self._add_error is not None:
            raise self._add_error
        self.items.append(asset)
        return asset

    def get(self, asset_id: UUID) -> Asset | None:
        self._require_active()
        self._events.append("asset.get")
        self.get_calls.append(asset_id)
        return next((asset for asset in self.items if asset.id == asset_id), None)

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Asset]:
        self._require_active()
        self._events.append("asset.list")
        self.list_calls.append((offset, limit))
        if self._list_error is not None:
            raise self._list_error
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive.")
        return self.items[offset : offset + limit]

    def delete(self, asset_id: UUID) -> None:
        self._require_active()
        self.delete_calls.append(asset_id)
        self.items[:] = [asset for asset in self.items if asset.id != asset_id]

    def exists(self, asset_id: UUID) -> bool:
        self._require_active()
        self.exists_calls.append(asset_id)
        return any(asset.id == asset_id for asset in self.items)

    def count(self) -> int:
        self._require_active()
        self.count_calls += 1
        return len(self.items)


class FakePriceHistoryRepository:
    """Record PriceHistory repository calls with controlled latest results."""

    def __init__(
        self,
        items: list[PriceHistory],
        latest_by_asset: dict[UUID, PriceHistory | None],
        is_active: Callable[[], bool],
        events: list[str],
        *,
        add_error: BaseException | None,
        latest_error: BaseException | None,
    ) -> None:
        self.items = items
        self.latest_by_asset = latest_by_asset
        self._is_active = is_active
        self._events = events
        self._add_error = add_error
        self._latest_error = latest_error
        self.added: list[PriceHistory] = []
        self.get_calls: list[UUID] = []
        self.latest_calls: list[UUID] = []
        self.list_calls: list[tuple[int, int]] = []
        self.delete_calls: list[UUID] = []
        self.exists_calls: list[UUID] = []
        self.count_calls = 0

    def _require_active(self) -> None:
        if not self._is_active():
            raise RuntimeError("Repository accessed outside its Unit of Work.")

    def add(self, price_history: PriceHistory) -> PriceHistory:
        self._require_active()
        self._events.append("price.add")
        self.added.append(price_history)
        if self._add_error is not None:
            raise self._add_error
        self.items.append(price_history)
        return price_history

    def get(self, price_history_id: UUID) -> PriceHistory | None:
        self._require_active()
        self.get_calls.append(price_history_id)
        return next(
            (price for price in self.items if price.id == price_history_id),
            None,
        )

    def get_latest_for_asset(self, asset_id: UUID) -> PriceHistory | None:
        self._require_active()
        self._events.append("price.latest")
        self.latest_calls.append(asset_id)
        if self._latest_error is not None:
            raise self._latest_error
        return self.latest_by_asset.get(asset_id)

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[PriceHistory]:
        self._require_active()
        self.list_calls.append((offset, limit))
        return self.items[offset : offset + limit]

    def delete(self, price_history: PriceHistory) -> None:
        self._require_active()
        self.delete_calls.append(price_history.id)
        self.items[:] = [price for price in self.items if price.id != price_history.id]

    def exists(self, price_history_id: UUID) -> bool:
        self._require_active()
        self.exists_calls.append(price_history_id)
        return any(price.id == price_history_id for price in self.items)

    def count(self) -> int:
        self._require_active()
        self.count_calls += 1
        return len(self.items)


class TrackingUnitOfWork:
    """Expose active-only fakes and record one service operation lifecycle."""

    def __init__(
        self,
        assets: list[Asset],
        prices: list[PriceHistory],
        latest_by_asset: dict[UUID, PriceHistory | None],
        *,
        asset_add_error: BaseException | None,
        asset_list_error: BaseException | None,
        price_add_error: BaseException | None,
        price_latest_error: BaseException | None,
    ) -> None:
        self.active = False
        self.enter_count = 0
        self.exit_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.exit_exceptions: list[type[BaseException] | None] = []
        self.events: list[str] = []
        self.asset_repository = FakeAssetRepository(
            assets,
            lambda: self.active,
            self.events,
            add_error=asset_add_error,
            list_error=asset_list_error,
        )
        self.price_repository = FakePriceHistoryRepository(
            prices,
            latest_by_asset,
            lambda: self.active,
            self.events,
            add_error=price_add_error,
            latest_error=price_latest_error,
        )

    @property
    def assets(self) -> FakeAssetRepository:
        self._require_active()
        return self.asset_repository

    @property
    def portfolios(self) -> PortfolioRepository:
        self._require_active()
        raise AssertionError("Portfolio repository is outside the tested workflow.")

    @property
    def price_history(self) -> FakePriceHistoryRepository:
        self._require_active()
        return self.price_repository

    @property
    def snapshots(self) -> SnapshotRepository:
        self._require_active()
        raise AssertionError("Snapshot repository is outside the tested workflow.")

    def __enter__(self) -> Self:
        if self.active:
            raise RuntimeError("Unit of Work is already active.")
        self.active = True
        self.enter_count += 1
        self.events.append("uow.enter")
        return self

    def commit(self) -> None:
        self._require_active()
        self.commit_count += 1
        self.events.append("uow.commit")

    def rollback(self) -> None:
        self._require_active()
        self.rollback_count += 1
        self.events.append("uow.rollback")

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
        self.events.append("uow.exit")
        self.active = False
        return False

    def _require_active(self) -> None:
        if not self.active:
            raise RuntimeError("Unit of Work is not active.")


class TrackingUnitOfWorkFactory:
    """Create a fresh UnitOfWork for every invocation over controlled state."""

    def __init__(
        self,
        *,
        assets: Sequence[Asset] = (),
        prices: Sequence[PriceHistory] = (),
        latest_by_asset: dict[UUID, PriceHistory | None] | None = None,
        asset_add_error: BaseException | None = None,
        asset_list_error: BaseException | None = None,
        price_add_error: BaseException | None = None,
        price_latest_error: BaseException | None = None,
    ) -> None:
        self.asset_state = list(assets)
        self.price_state = list(prices)
        self.latest_by_asset = dict(latest_by_asset or {})
        self.asset_add_error = asset_add_error
        self.asset_list_error = asset_list_error
        self.price_add_error = price_add_error
        self.price_latest_error = price_latest_error
        self.created: list[TrackingUnitOfWork] = []

    def __call__(self) -> TrackingUnitOfWork:
        unit_of_work = TrackingUnitOfWork(
            self.asset_state,
            self.price_state,
            self.latest_by_asset,
            asset_add_error=self.asset_add_error,
            asset_list_error=self.asset_list_error,
            price_add_error=self.price_add_error,
            price_latest_error=self.price_latest_error,
        )
        self.created.append(unit_of_work)
        return unit_of_work


def make_asset(
    *,
    identity: int,
    symbol: str,
    currency: Currency = Currency.USD,
) -> Asset:
    return Asset(
        id=UUID(int=identity),
        symbol=symbol,
        name=f"{symbol} Asset",
        asset_type=AssetType.EQUITY,
        currency=currency,
        created_at=NOW,
    )


def make_price(
    asset: Asset,
    *,
    identity: int,
    price: Decimal,
    observed_at: datetime = NOW,
) -> PriceHistory:
    return PriceHistory(
        id=UUID(int=identity),
        asset_id=asset.id,
        price=price,
        currency=asset.currency,
        observed_at=observed_at,
    )


def only_unit_of_work(factory: TrackingUnitOfWorkFactory) -> TrackingUnitOfWork:
    assert len(factory.created) == 1
    return factory.created[0]


def test_default_services_implement_contracts_and_are_publicly_exported() -> None:
    factory = TrackingUnitOfWorkFactory()
    asset_service = DefaultAssetApplicationService(factory)
    market_price_service = DefaultMarketPriceApplicationService(factory)

    assert isinstance(asset_service, AssetApplicationService)
    assert isinstance(market_price_service, MarketPriceApplicationService)
    assert not inspect.isabstract(DefaultAssetApplicationService)
    assert not inspect.isabstract(DefaultMarketPriceApplicationService)
    assert application.DefaultAssetApplicationService is DefaultAssetApplicationService
    assert application.DefaultMarketPriceApplicationService is DefaultMarketPriceApplicationService
    assert get_type_hints(DefaultAssetApplicationService.__init__) == {
        "unit_of_work_factory": Callable[[], UnitOfWork],
        "return": type(None),
    }
    assert get_type_hints(DefaultMarketPriceApplicationService.__init__) == {
        "unit_of_work_factory": Callable[[], UnitOfWork],
        "return": type(None),
    }


def test_create_asset_normalizes_domain_values_adds_once_and_commits_once() -> None:
    factory = TrackingUnitOfWorkFactory()
    service = DefaultAssetApplicationService(factory)

    result = service.create_asset(
        CreateAssetCommand(
            symbol="  alpha  ",
            name="  Alpha Asset  ",
            asset_type=AssetType.ETF,
            currency=Currency.EUR,
        )
    )

    unit_of_work = only_unit_of_work(factory)
    repository = unit_of_work.asset_repository
    assert len(repository.added) == 1
    asset = repository.added[0]
    assert isinstance(asset, Asset)
    assert asset.symbol == "ALPHA"
    assert asset.name == "Alpha Asset"
    assert asset.asset_type is AssetType.ETF
    assert asset.currency is Currency.EUR
    assert asset.id.int != 0
    assert asset.is_active
    assert result == AssetView(
        id=asset.id,
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type,
        currency=asset.currency,
        is_active=asset.is_active,
        created_at=asset.created_at,
    )
    assert result.id is asset.id
    assert result.created_at is asset.created_at
    assert repository.get_calls == []
    assert repository.exists_calls == []
    assert unit_of_work.commit_count == 1
    assert unit_of_work.enter_count == unit_of_work.exit_count == 1
    assert unit_of_work.rollback_count == 0
    assert unit_of_work.events == ["uow.enter", "asset.add", "uow.commit", "uow.exit"]


def test_create_asset_allows_duplicate_normalized_symbols_with_fresh_units_of_work() -> None:
    factory = TrackingUnitOfWorkFactory()
    service = DefaultAssetApplicationService(factory)

    first = service.create_asset(
        CreateAssetCommand(
            symbol=" same ",
            name="First",
            asset_type=AssetType.EQUITY,
            currency=Currency.TRY,
        )
    )
    second = service.create_asset(
        CreateAssetCommand(
            symbol="SAME",
            name="Second",
            asset_type=AssetType.FUND,
            currency=Currency.GBP,
        )
    )

    assert first.symbol == second.symbol == "SAME"
    assert first.id != second.id
    assert len(factory.created) == 2
    assert factory.created[0] is not factory.created[1]
    for unit_of_work in factory.created:
        assert unit_of_work.commit_count == 1
        assert unit_of_work.asset_repository.get_calls == []
        assert unit_of_work.asset_repository.exists_calls == []
        assert not hasattr(unit_of_work.asset_repository, "get_by_symbol")


@pytest.mark.parametrize(
    ("symbol", "name", "message"),
    (
        ("   ", "Valid", "symbol cannot be empty"),
        ("VALID", "   ", "name cannot be empty"),
    ),
)
def test_create_asset_preserves_domain_validation_failures(
    symbol: str,
    name: str,
    message: str,
) -> None:
    factory = TrackingUnitOfWorkFactory()
    service = DefaultAssetApplicationService(factory)

    with pytest.raises(ValueError, match=message):
        service.create_asset(
            CreateAssetCommand(
                symbol=symbol,
                name=name,
                asset_type=AssetType.EQUITY,
                currency=Currency.USD,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.asset_repository.added == []
    assert unit_of_work.commit_count == 0
    assert unit_of_work.rollback_count == 0
    assert unit_of_work.exit_exceptions == [ValueError]


def test_create_asset_repository_failure_propagates_without_commit() -> None:
    failure = RepositoryError("asset add failed")
    factory = TrackingUnitOfWorkFactory(asset_add_error=failure)
    service = DefaultAssetApplicationService(factory)

    with pytest.raises(RepositoryError) as raised:
        service.create_asset(
            CreateAssetCommand(
                symbol="FAIL",
                name="Failure",
                asset_type=AssetType.BOND,
                currency=Currency.GBP,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert raised.value is failure
    assert len(unit_of_work.asset_repository.added) == 1
    assert unit_of_work.commit_count == 0
    assert unit_of_work.exit_exceptions == [RepositoryError]


def test_list_assets_forwards_pagination_preserves_order_and_returns_tuple() -> None:
    second = make_asset(identity=2, symbol="SECOND")
    first = make_asset(identity=1, symbol="FIRST")
    third = make_asset(identity=3, symbol="THIRD")
    factory = TrackingUnitOfWorkFactory(assets=(second, first, third))
    service = DefaultAssetApplicationService(factory)

    result = service.list_assets(ListAssetsQuery(offset=1, limit=2))

    unit_of_work = only_unit_of_work(factory)
    assert isinstance(result, tuple)
    assert tuple(view.id for view in result) == (first.id, third.id)
    assert all(isinstance(view, AssetView) for view in result)
    assert unit_of_work.asset_repository.list_calls == [(1, 2)]
    assert unit_of_work.asset_repository.count_calls == 0
    assert unit_of_work.asset_repository.get_calls == []
    assert unit_of_work.commit_count == 0
    assert unit_of_work.events == ["uow.enter", "asset.list", "uow.exit"]


def test_list_assets_empty_page_and_invalid_pagination_do_not_commit() -> None:
    empty_factory = TrackingUnitOfWorkFactory()
    service = DefaultAssetApplicationService(empty_factory)

    assert service.list_assets(ListAssetsQuery()) == ()
    assert only_unit_of_work(empty_factory).commit_count == 0

    invalid_factory = TrackingUnitOfWorkFactory()
    invalid_service = DefaultAssetApplicationService(invalid_factory)
    with pytest.raises(ValueError, match="offset must be non-negative"):
        invalid_service.list_assets(ListAssetsQuery(offset=-1, limit=100))

    unit_of_work = only_unit_of_work(invalid_factory)
    assert unit_of_work.asset_repository.list_calls == [(-1, 100)]
    assert unit_of_work.commit_count == 0
    assert unit_of_work.exit_exceptions == [ValueError]


def test_record_market_price_uses_asset_currency_and_preserves_exact_values() -> None:
    asset = make_asset(identity=10, symbol="GBP", currency=Currency.GBP)
    exact_price = Decimal("123456789.000000000123400")
    factory = TrackingUnitOfWorkFactory(assets=(asset,))
    service = DefaultMarketPriceApplicationService(factory)

    result = service.record_market_price(
        RecordMarketPriceCommand(
            asset_id=asset.id,
            price=exact_price,
            observed_at=NOW,
        )
    )

    unit_of_work = only_unit_of_work(factory)
    repository = unit_of_work.price_repository
    assert unit_of_work.asset_repository.get_calls == [asset.id]
    assert len(repository.added) == 1
    price_history = repository.added[0]
    assert isinstance(price_history, PriceHistory)
    assert price_history.asset_id is asset.id
    assert price_history.price is exact_price
    assert str(price_history.price) == "123456789.000000000123400"
    assert price_history.currency is Currency.GBP
    assert price_history.observed_at is NOW
    assert result == MarketPriceView(
        id=price_history.id,
        asset_id=asset.id,
        price=exact_price,
        currency=Currency.GBP,
        observed_at=NOW,
    )
    assert result.price is exact_price
    assert result.observed_at is NOW
    assert result.observed_at.tzinfo is UTC
    assert result.observed_at.microsecond == 654321
    assert repository.latest_calls == []
    assert unit_of_work.commit_count == 1
    assert unit_of_work.events == [
        "uow.enter",
        "asset.get",
        "price.add",
        "uow.commit",
        "uow.exit",
    ]


def test_record_market_price_accepts_exact_zero() -> None:
    asset = make_asset(identity=20, symbol="ZERO")
    zero = Decimal("0")
    factory = TrackingUnitOfWorkFactory(assets=(asset,))
    service = DefaultMarketPriceApplicationService(factory)

    result = service.record_market_price(
        RecordMarketPriceCommand(
            asset_id=asset.id,
            price=zero,
            observed_at=NOW,
        )
    )

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.price_repository.added[0].price is zero
    assert result.price is zero
    assert unit_of_work.commit_count == 1


def test_record_market_price_preserves_domain_naive_datetime_normalization() -> None:
    asset = make_asset(identity=30, symbol="NAIVE")
    naive = datetime(2026, 7, 20, 11, 22, 33, 444555)
    factory = TrackingUnitOfWorkFactory(assets=(asset,))
    service = DefaultMarketPriceApplicationService(factory)

    result = service.record_market_price(
        RecordMarketPriceCommand(
            asset_id=asset.id,
            price=Decimal("30"),
            observed_at=naive,
        )
    )

    unit_of_work = only_unit_of_work(factory)
    persisted = unit_of_work.price_repository.added[0]
    assert persisted.observed_at.tzinfo is UTC
    assert persisted.observed_at.replace(tzinfo=None) == naive
    assert result.observed_at == persisted.observed_at
    assert result.observed_at != NOW
    assert unit_of_work.commit_count == 1


def test_record_market_price_missing_asset_does_not_add_or_commit() -> None:
    missing_id = UUID(int=999)
    factory = TrackingUnitOfWorkFactory()
    service = DefaultMarketPriceApplicationService(factory)

    with pytest.raises(AssetNotFoundError, match=str(missing_id)):
        service.record_market_price(
            RecordMarketPriceCommand(
                asset_id=missing_id,
                price=Decimal("1"),
                observed_at=NOW,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.asset_repository.get_calls == [missing_id]
    assert unit_of_work.price_repository.added == []
    assert unit_of_work.price_repository.latest_calls == []
    assert unit_of_work.commit_count == 0
    assert unit_of_work.exit_exceptions == [AssetNotFoundError]


def test_record_market_price_negative_domain_failure_does_not_add_or_commit() -> None:
    asset = make_asset(identity=40, symbol="NEGATIVE")
    factory = TrackingUnitOfWorkFactory(assets=(asset,))
    service = DefaultMarketPriceApplicationService(factory)

    with pytest.raises(ValueError, match="cannot be negative"):
        service.record_market_price(
            RecordMarketPriceCommand(
                asset_id=asset.id,
                price=Decimal("-0.01"),
                observed_at=NOW,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert unit_of_work.price_repository.added == []
    assert unit_of_work.commit_count == 0
    assert unit_of_work.exit_exceptions == [ValueError]


def test_record_market_price_repository_failure_propagates_without_commit() -> None:
    asset = make_asset(identity=50, symbol="FAIL")
    failure = RepositoryError("price add failed")
    factory = TrackingUnitOfWorkFactory(
        assets=(asset,),
        price_add_error=failure,
    )
    service = DefaultMarketPriceApplicationService(factory)

    with pytest.raises(RepositoryError) as raised:
        service.record_market_price(
            RecordMarketPriceCommand(
                asset_id=asset.id,
                price=Decimal("50"),
                observed_at=NOW,
            )
        )

    unit_of_work = only_unit_of_work(factory)
    assert raised.value is failure
    assert len(unit_of_work.price_repository.added) == 1
    assert unit_of_work.commit_count == 0
    assert unit_of_work.exit_exceptions == [RepositoryError]


def test_get_latest_market_price_loads_asset_first_maps_and_does_not_commit() -> None:
    asset = make_asset(identity=60, symbol="LATEST", currency=Currency.EUR)
    latest = make_price(
        asset,
        identity=61,
        price=Decimal("60.12500"),
    )
    factory = TrackingUnitOfWorkFactory(
        assets=(asset,),
        latest_by_asset={asset.id: latest},
    )
    service = DefaultMarketPriceApplicationService(factory)

    result = service.get_latest_market_price(GetLatestMarketPriceQuery(asset_id=asset.id))

    unit_of_work = only_unit_of_work(factory)
    assert result == MarketPriceView(
        id=latest.id,
        asset_id=asset.id,
        price=latest.price,
        currency=Currency.EUR,
        observed_at=NOW,
    )
    assert unit_of_work.asset_repository.get_calls == [asset.id]
    assert unit_of_work.price_repository.latest_calls == [asset.id]
    assert unit_of_work.price_repository.added == []
    assert unit_of_work.price_repository.list_calls == []
    assert unit_of_work.price_repository.count_calls == 0
    assert unit_of_work.commit_count == 0
    assert unit_of_work.events == [
        "uow.enter",
        "asset.get",
        "price.latest",
        "uow.exit",
    ]


def test_get_latest_market_price_distinguishes_no_history_from_unknown_asset() -> None:
    asset = make_asset(identity=70, symbol="EMPTY")
    existing_factory = TrackingUnitOfWorkFactory(assets=(asset,))
    service = DefaultMarketPriceApplicationService(existing_factory)

    assert service.get_latest_market_price(GetLatestMarketPriceQuery(asset_id=asset.id)) is None
    existing_unit_of_work = only_unit_of_work(existing_factory)
    assert existing_unit_of_work.price_repository.latest_calls == [asset.id]
    assert existing_unit_of_work.commit_count == 0

    missing_id = UUID(int=999)
    missing_factory = TrackingUnitOfWorkFactory()
    missing_service = DefaultMarketPriceApplicationService(missing_factory)
    with pytest.raises(AssetNotFoundError, match=str(missing_id)):
        missing_service.get_latest_market_price(GetLatestMarketPriceQuery(asset_id=missing_id))

    missing_unit_of_work = only_unit_of_work(missing_factory)
    assert missing_unit_of_work.price_repository.latest_calls == []
    assert missing_unit_of_work.commit_count == 0


def test_get_latest_market_price_preserves_zero_without_truthiness_fallback() -> None:
    asset = make_asset(identity=80, symbol="ZERO")
    latest = make_price(
        asset,
        identity=81,
        price=Decimal("0"),
    )
    factory = TrackingUnitOfWorkFactory(
        assets=(asset,),
        latest_by_asset={asset.id: latest},
    )
    service = DefaultMarketPriceApplicationService(factory)

    result = service.get_latest_market_price(GetLatestMarketPriceQuery(asset_id=asset.id))

    assert result is not None
    assert result.price == Decimal("0")
    assert only_unit_of_work(factory).commit_count == 0


def test_latest_reads_use_uuid_with_duplicate_symbols_and_fresh_units_of_work() -> None:
    first_asset = make_asset(identity=90, symbol="DUP")
    second_asset = make_asset(identity=91, symbol="dup")
    first_price = make_price(first_asset, identity=92, price=Decimal("90"))
    second_price = make_price(second_asset, identity=93, price=Decimal("91"))
    factory = TrackingUnitOfWorkFactory(
        assets=(first_asset, second_asset),
        latest_by_asset={
            first_asset.id: first_price,
            second_asset.id: second_price,
        },
    )
    service = DefaultMarketPriceApplicationService(factory)

    first_result = service.get_latest_market_price(
        GetLatestMarketPriceQuery(asset_id=first_asset.id)
    )
    second_result = service.get_latest_market_price(
        GetLatestMarketPriceQuery(asset_id=second_asset.id)
    )
    repeated_result = service.get_latest_market_price(
        GetLatestMarketPriceQuery(asset_id=first_asset.id)
    )

    assert first_asset.symbol == second_asset.symbol
    assert first_result is not None
    assert second_result is not None
    assert first_result.asset_id == first_asset.id
    assert second_result.asset_id == second_asset.id
    assert repeated_result == first_result
    assert len(factory.created) == 3
    assert len({id(unit_of_work) for unit_of_work in factory.created}) == 3
    assert [unit_of_work.price_repository.latest_calls for unit_of_work in factory.created] == [
        [first_asset.id],
        [second_asset.id],
        [first_asset.id],
    ]
    assert all(unit_of_work.commit_count == 0 for unit_of_work in factory.created)


def test_latest_price_repository_failure_propagates_without_commit() -> None:
    asset = make_asset(identity=100, symbol="FAIL")
    failure = RepositoryError("latest read failed")
    factory = TrackingUnitOfWorkFactory(
        assets=(asset,),
        price_latest_error=failure,
    )
    service = DefaultMarketPriceApplicationService(factory)

    with pytest.raises(RepositoryError) as raised:
        service.get_latest_market_price(GetLatestMarketPriceQuery(asset_id=asset.id))

    unit_of_work = only_unit_of_work(factory)
    assert raised.value is failure
    assert unit_of_work.price_repository.latest_calls == [asset.id]
    assert unit_of_work.commit_count == 0
    assert unit_of_work.exit_exceptions == [RepositoryError]


def test_default_services_remain_framework_independent_and_narrow() -> None:
    source = SERVICES_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SERVICES_MODULE))
    imported_modules = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    asset_source = inspect.getsource(DefaultAssetApplicationService)
    market_price_source = inspect.getsource(DefaultMarketPriceApplicationService)
    concrete_source = asset_source + market_price_source
    forbidden_imports = {
        "sqlalchemy",
        "sqlite3",
        "alembic",
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
    for forbidden_behavior in (
        "PortfolioPositionCalculator",
        "PortfolioValuationCalculator",
        "PortfolioDashboard",
        "get_by_symbol",
        "symbol ==",
        ".exists(",
        ".count(",
        ".rollback(",
        "except Exception",
        "float(",
        ".quantize(",
        "round(",
        "datetime.now",
        "select(",
        "Session",
        "._transactions",
        "._assets",
    ):
        assert forbidden_behavior not in concrete_source
    assert asset_source.count("unit_of_work.commit()") == 1
    assert market_price_source.count("unit_of_work.commit()") == 1
