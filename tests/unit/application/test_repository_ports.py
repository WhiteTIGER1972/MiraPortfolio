"""Contract and architecture tests for application repository ports."""

import ast
import inspect
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Literal, Self, get_type_hints
from uuid import UUID

from app.application.ports import (
    AssetRepository,
    PortfolioRepository,
    PriceHistoryRepository,
    SnapshotRepository,
)
from app.application.unit_of_work import UnitOfWork
from app.domain.entities.asset import Asset
from app.domain.entities.portfolio import Portfolio
from app.domain.entities.price_history import PriceHistory
from app.domain.entities.snapshot import Snapshot

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PORTS_MODULE = PROJECT_ROOT / "app/application/ports/repositories.py"
UNIT_OF_WORK_MODULE = PROJECT_ROOT / "app/application/unit_of_work.py"
DOMAIN_ROOT = PROJECT_ROOT / "app/domain"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_repository_ports_declare_exact_supported_operations() -> None:
    assert AssetRepository.__abstractmethods__ == {
        "add",
        "count",
        "delete",
        "exists",
        "get",
        "list",
    }
    assert PortfolioRepository.__abstractmethods__ == {
        "add",
        "count",
        "delete",
        "exists",
        "get",
        "list",
        "save",
    }
    assert PriceHistoryRepository.__abstractmethods__ == {
        "add",
        "count",
        "delete",
        "exists",
        "get",
        "get_latest_for_asset",
        "list",
    }
    assert SnapshotRepository.__abstractmethods__ == {
        "add",
        "count",
        "delete",
        "exists",
        "get",
        "list",
    }


def test_asset_repository_uses_uuid_identity_without_symbol_lookup() -> None:
    assert get_type_hints(AssetRepository.get) == {
        "asset_id": UUID,
        "return": Asset | None,
    }
    assert get_type_hints(AssetRepository.delete) == {
        "asset_id": UUID,
        "return": type(None),
    }
    assert get_type_hints(AssetRepository.exists) == {
        "asset_id": UUID,
        "return": bool,
    }
    assert not hasattr(AssetRepository, "get_by_symbol")
    assert "symbol" not in PORTS_MODULE.read_text(encoding="utf-8")


def test_repository_list_contracts_preserve_pagination_and_sequence_results() -> None:
    expected_parameters = ("self", "offset", "limit")
    list_contracts = (
        (AssetRepository.list, Sequence[Asset]),
        (PortfolioRepository.list, Sequence[Portfolio]),
        (PriceHistoryRepository.list, Sequence[PriceHistory]),
        (SnapshotRepository.list, Sequence[Snapshot]),
    )
    for list_method, expected_return in list_contracts:
        signature = inspect.signature(list_method)
        assert tuple(signature.parameters) == expected_parameters
        assert signature.parameters["offset"].default == 0
        assert signature.parameters["offset"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["limit"].default == 100
        assert signature.parameters["limit"].kind is inspect.Parameter.KEYWORD_ONLY
        assert get_type_hints(list_method)["return"] == expected_return


def test_price_history_repository_declares_uuid_latest_price_lookup() -> None:
    method = PriceHistoryRepository.get_latest_for_asset

    assert tuple(inspect.signature(method).parameters) == ("self", "asset_id")
    assert get_type_hints(method) == {
        "asset_id": UUID,
        "return": PriceHistory | None,
    }
    assert not {
        "symbol",
        "currency",
        "portfolio_id",
        "provider",
        "date",
        "limit",
    } & set(inspect.signature(method).parameters)


def test_portfolio_add_and_save_are_distinct_required_operations() -> None:
    assert PortfolioRepository.add is not PortfolioRepository.save
    assert get_type_hints(PortfolioRepository.add) == {
        "portfolio": Portfolio,
        "return": Portfolio,
    }
    assert get_type_hints(PortfolioRepository.save) == {
        "portfolio": Portfolio,
        "return": Portfolio,
    }
    assert inspect.getsource(PortfolioRepository.save) != inspect.getsource(PortfolioRepository.add)


def test_unit_of_work_exposes_explicit_repository_port_types() -> None:
    expected_types = {
        "assets": AssetRepository,
        "portfolios": PortfolioRepository,
        "price_history": PriceHistoryRepository,
        "snapshots": SnapshotRepository,
    }
    for property_name, expected_type in expected_types.items():
        descriptor = inspect.getattr_static(UnitOfWork, property_name)
        assert isinstance(descriptor, property)
        assert descriptor.fget is not None
        assert get_type_hints(descriptor.fget)["return"] is expected_type


def test_unit_of_work_lifecycle_contract_is_unchanged() -> None:
    assert get_type_hints(UnitOfWork.__enter__) == {"return": Self}
    assert get_type_hints(UnitOfWork.commit) == {"return": type(None)}
    assert get_type_hints(UnitOfWork.rollback) == {"return": type(None)}
    assert get_type_hints(UnitOfWork.__exit__) == {
        "exception_type": type[BaseException] | None,
        "exception": BaseException | None,
        "traceback": TracebackType | None,
        "return": Literal[False],
    }


def test_repository_ports_and_unit_of_work_are_framework_independent() -> None:
    forbidden = {
        "sqlalchemy",
        "sqlite3",
        "alembic",
        "PySide6",
        "app.infrastructure",
        "app.ui",
    }
    imports = imported_modules(PORTS_MODULE) | imported_modules(UNIT_OF_WORK_MODULE)

    assert not {
        imported
        for imported in imports
        if any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in forbidden)
    }
    for domain_module in DOMAIN_ROOT.rglob("*.py"):
        assert "app.application.ports" not in imported_modules(domain_module)
