"""Contract and architecture tests for market-price and dashboard services."""

import ast
import inspect
from pathlib import Path
from typing import get_type_hints

import app.application as application
from app.application.commands import RecordMarketPriceCommand
from app.application.queries import (
    GetLatestMarketPriceQuery,
    GetPortfolioDashboardQuery,
)
from app.application.results import (
    CurrencyValuationView,
    MarketPriceView,
    PortfolioDashboard,
    ValuedAssetPositionView,
)
from app.application.services import (
    MarketPriceApplicationService,
    PortfolioDashboardQueryService,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICES_MODULE = PROJECT_ROOT / "app/application/services.py"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_market_price_application_service_declares_exact_abstract_contract() -> None:
    assert inspect.isabstract(MarketPriceApplicationService)
    assert MarketPriceApplicationService.__abstractmethods__ == {
        "get_latest_market_price",
        "record_market_price",
    }
    assert get_type_hints(MarketPriceApplicationService.record_market_price) == {
        "command": RecordMarketPriceCommand,
        "return": MarketPriceView,
    }
    assert get_type_hints(MarketPriceApplicationService.get_latest_market_price) == {
        "query": GetLatestMarketPriceQuery,
        "return": MarketPriceView | None,
    }
    assert tuple(
        inspect.signature(MarketPriceApplicationService.record_market_price).parameters
    ) == ("self", "command")
    assert tuple(
        inspect.signature(MarketPriceApplicationService.get_latest_market_price).parameters
    ) == ("self", "query")


def test_dashboard_query_service_declares_exact_abstract_contract() -> None:
    assert inspect.isabstract(PortfolioDashboardQueryService)
    assert PortfolioDashboardQueryService.__abstractmethods__ == {"get_dashboard"}
    assert get_type_hints(PortfolioDashboardQueryService.get_dashboard) == {
        "query": GetPortfolioDashboardQuery,
        "return": PortfolioDashboard,
    }
    assert tuple(inspect.signature(PortfolioDashboardQueryService.get_dashboard).parameters) == (
        "self",
        "query",
    )


def test_market_price_and_dashboard_contracts_are_publicly_exported() -> None:
    assert application.RecordMarketPriceCommand is RecordMarketPriceCommand
    assert application.GetLatestMarketPriceQuery is GetLatestMarketPriceQuery
    assert application.GetPortfolioDashboardQuery is GetPortfolioDashboardQuery
    assert application.MarketPriceView is MarketPriceView
    assert application.ValuedAssetPositionView is ValuedAssetPositionView
    assert application.CurrencyValuationView is CurrencyValuationView
    assert application.PortfolioDashboard is PortfolioDashboard
    assert application.MarketPriceApplicationService is MarketPriceApplicationService
    assert application.PortfolioDashboardQueryService is PortfolioDashboardQueryService


def test_new_service_contracts_have_no_implementation_or_forbidden_dependency() -> None:
    forbidden = {
        "sqlalchemy",
        "sqlite3",
        "alembic",
        "PySide6",
        "app.infrastructure",
        "app.ui",
    }
    imports = imported_modules(SERVICES_MODULE)
    market_price_source = inspect.getsource(MarketPriceApplicationService)
    dashboard_source = inspect.getsource(PortfolioDashboardQueryService)
    contract_source = market_price_source + dashboard_source

    assert not {
        imported
        for imported in imports
        if any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in forbidden)
    }
    for forbidden_behavior in (
        "UnitOfWork",
        "Repository",
        "PortfolioPositionCalculator",
        "PortfolioValuationCalculator",
        "MissingMarketPriceError",
        "sorted(",
        "order_by",
        "commit(",
        "get_by_symbol",
        "Decimal(",
        "market_value =",
        "unrealized_pnl =",
    ):
        assert forbidden_behavior not in contract_source
    assert "Deterministic selection ordering belongs to" in market_price_source
    assert "def __init__" not in contract_source
