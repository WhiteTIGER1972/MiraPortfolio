"""Contract and architecture tests for Asset application services."""

import ast
import inspect
from pathlib import Path
from typing import get_type_hints

import app.application as application
from app.application.commands import CreateAssetCommand
from app.application.queries import ListAssetsQuery
from app.application.results import AssetView
from app.application.services import AssetApplicationService

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


def test_asset_application_service_declares_exact_abstract_contract() -> None:
    assert inspect.isabstract(AssetApplicationService)
    assert AssetApplicationService.__abstractmethods__ == {"create_asset", "list_assets"}
    assert tuple(inspect.signature(AssetApplicationService.create_asset).parameters) == (
        "self",
        "command",
    )
    assert get_type_hints(AssetApplicationService.create_asset) == {
        "command": CreateAssetCommand,
        "return": AssetView,
    }
    assert tuple(inspect.signature(AssetApplicationService.list_assets).parameters) == (
        "self",
        "query",
    )
    assert get_type_hints(AssetApplicationService.list_assets) == {
        "query": ListAssetsQuery,
        "return": tuple[AssetView, ...],
    }


def test_asset_contracts_are_publicly_exported() -> None:
    assert application.CreateAssetCommand is CreateAssetCommand
    assert application.ListAssetsQuery is ListAssetsQuery
    assert application.AssetView is AssetView
    assert application.AssetApplicationService is AssetApplicationService


def test_asset_application_service_is_framework_independent_and_contract_only() -> None:
    forbidden = {
        "sqlalchemy",
        "sqlite3",
        "alembic",
        "PySide6",
        "app.infrastructure",
        "app.ui",
    }
    imports = imported_modules(SERVICES_MODULE)
    contract_source = inspect.getsource(AssetApplicationService)

    assert not {
        imported
        for imported in imports
        if any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in forbidden)
    }
    assert "UnitOfWork" not in contract_source
    assert "Repository" not in contract_source
    assert "get_by_symbol" not in contract_source
    assert "market_price" not in contract_source
    assert "def __init__" not in contract_source
