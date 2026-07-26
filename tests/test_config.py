"""Tests for side-effect-free release runtime path settings."""

import os
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath

import pytest
from sqlalchemy.engine import make_url

from app.core import config, runtime_paths
from app.core.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent developer environment settings and cache state from leaking into tests."""
    for name in tuple(os.environ):
        if name.startswith("MIRA_"):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def isolated_runtime_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Path]:
    """Redirect platform path providers without creating any runtime directories."""
    roots = {
        "data": tmp_path / "profile" / "data",
        "cache": tmp_path / "profile" / "cache",
        "logs": tmp_path / "profile" / "logs",
    }
    monkeypatch.setattr(runtime_paths, "default_data_directory", lambda: roots["data"])
    monkeypatch.setattr(runtime_paths, "default_cache_directory", lambda: roots["cache"])
    monkeypatch.setattr(runtime_paths, "default_log_directory", lambda: roots["logs"])
    return roots


def test_default_runtime_paths_are_absolute(
    isolated_runtime_roots: dict[str, Path],
) -> None:
    settings = Settings(_env_file=None)

    assert all(
        path.is_absolute()
        for path in (
            settings.data_directory,
            settings.cache_directory,
            settings.database_directory,
            settings.export_directory,
            settings.backup_directory,
            settings.log_directory,
            settings.database_path,
        )
    )


def test_default_runtime_paths_do_not_depend_on_current_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_runtime_roots: dict[str, Path],
) -> None:
    first_working_directory = tmp_path / "first-working-directory"
    second_working_directory = tmp_path / "second-working-directory"
    first_working_directory.mkdir()
    second_working_directory.mkdir()

    monkeypatch.chdir(first_working_directory)
    first = Settings(_env_file=None)
    monkeypatch.chdir(second_working_directory)
    second = Settings(_env_file=None)

    assert first.model_dump() == second.model_dump()


def test_default_data_hierarchy_and_database_url(
    isolated_runtime_roots: dict[str, Path],
) -> None:
    settings = Settings(_env_file=None)
    parsed_url = make_url(settings.database_url)

    assert settings.database_directory == (settings.data_directory / config.DATABASE_DIRECTORY_NAME)
    assert settings.export_directory == (settings.data_directory / config.EXPORT_DIRECTORY_NAME)
    assert settings.backup_directory == (settings.data_directory / config.BACKUP_DIRECTORY_NAME)
    assert settings.database_path == (settings.database_directory / config.DATABASE_FILENAME)
    assert parsed_url.drivername == "sqlite"
    assert parsed_url.database is not None
    assert Path(parsed_url.database) == settings.database_path


def test_windows_database_path_round_trips_through_sqlalchemy_url() -> None:
    database_path = PureWindowsPath(
        "C:/Users/Mira User/AppData/Local/Mira/Mira Portfolio/database/portfolio.db"
    )

    database_url = runtime_paths.sqlite_url_for_path(database_path)
    parsed_url = make_url(database_url)

    assert parsed_url.drivername == "sqlite"
    assert parsed_url.database == str(database_path)


def test_database_url_environment_override_is_preserved_exactly(
    monkeypatch: pytest.MonkeyPatch,
    isolated_runtime_roots: dict[str, Path],
) -> None:
    override = "postgresql+psycopg://mira:secret@db.example/mira?sslmode=require"
    monkeypatch.setenv("MIRA_DATABASE_URL", override)

    settings = Settings(_env_file=None)

    assert settings.database_url == override
    assert settings.database_path == (settings.database_directory / config.DATABASE_FILENAME)


def test_custom_directory_values_are_respected(tmp_path: Path) -> None:
    expected = {
        "data_directory": tmp_path / "custom-data",
        "cache_directory": tmp_path / "custom-cache",
        "database_directory": tmp_path / "custom-database",
        "export_directory": tmp_path / "custom-exports",
        "backup_directory": tmp_path / "custom-backups",
        "log_directory": tmp_path / "custom-logs",
    }
    custom = {field_name: str(path) for field_name, path in expected.items()}

    settings = Settings(_env_file=None, **custom)

    for field_name, expected_path in expected.items():
        assert getattr(settings, field_name) == expected_path
    assert settings.database_path == (expected["database_directory"] / config.DATABASE_FILENAME)


def test_custom_data_directory_drives_default_data_children(tmp_path: Path) -> None:
    data_directory = tmp_path / "custom-data-root"

    settings = Settings(_env_file=None, data_directory=data_directory)

    assert settings.database_directory.parent == data_directory
    assert settings.export_directory.parent == data_directory
    assert settings.backup_directory.parent == data_directory


def test_settings_construction_creates_no_directories_or_files(
    isolated_runtime_roots: dict[str, Path],
) -> None:
    settings = Settings(_env_file=None)

    assert not settings.data_directory.exists()
    assert not settings.cache_directory.exists()
    assert not settings.database_directory.exists()
    assert not settings.export_directory.exists()
    assert not settings.backup_directory.exists()
    assert not settings.log_directory.exists()
    assert not settings.database_path.exists()
