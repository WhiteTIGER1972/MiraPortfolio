"""Static architecture tests for the Sprint 1.4 persistence boundary."""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
APPLICATION_UNIT_OF_WORK = PROJECT_ROOT / "app/application/unit_of_work.py"
PERSISTENCE_ROOT = PROJECT_ROOT / "app/infrastructure/persistence"
REPOSITORIES = PERSISTENCE_ROOT / "sqlalchemy/repositories.py"
SQLALCHEMY_UNIT_OF_WORK = PERSISTENCE_ROOT / "sqlalchemy/unit_of_work.py"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_application_unit_of_work_is_framework_independent() -> None:
    imports = imported_modules(APPLICATION_UNIT_OF_WORK)
    forbidden = {
        "sqlalchemy",
        "sqlite3",
        "alembic",
        "PySide6",
        "app.infrastructure",
        "app.ui",
    }

    assert not {
        imported
        for imported in imports
        if any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in forbidden)
    }


def test_persistence_infrastructure_has_no_ui_async_thread_or_network_dependencies() -> None:
    forbidden = {
        "app.ui",
        "PySide6",
        "asyncio",
        "threading",
        "multiprocessing",
        "socket",
        "requests",
        "httpx",
        "urllib",
    }
    violations: set[str] = set()
    for path in PERSISTENCE_ROOT.rglob("*.py"):
        for imported in imported_modules(path):
            if any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in forbidden):
                violations.add(f"{path.relative_to(PROJECT_ROOT)}:{imported}")

    assert violations == set()


def test_repositories_do_not_own_sessions_transactions_or_engines() -> None:
    calls = called_names(REPOSITORIES)

    assert calls.isdisjoint(
        {
            "commit",
            "rollback",
            "close",
            "create_engine",
            "sessionmaker",
            "create_persistence_engine",
            "create_persistence_session_factory",
        }
    )
    assert "TransactionRepository" not in REPOSITORIES.read_text(encoding="utf-8")


def test_unit_of_work_has_no_event_or_background_behavior() -> None:
    imports = imported_modules(SQLALCHEMY_UNIT_OF_WORK)
    calls = called_names(SQLALCHEMY_UNIT_OF_WORK)
    source = SQLALCHEMY_UNIT_OF_WORK.read_text(encoding="utf-8")

    assert not any("event" in imported.lower() for imported in imports)
    assert calls.isdisjoint({"publish", "dispatch", "create_task", "Thread"})
    assert "scoped_session" not in source
