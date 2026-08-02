"""Integration coverage for release-safe database preparation."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError
from app.core.settings import Settings
from app.infrastructure.persistence import alembic_support, database_preparation
from app.infrastructure.persistence.alembic_support import (
    DEFAULT_MIGRATION_SCRIPT_LOCATION,
    create_alembic_config,
)
from app.infrastructure.persistence.database_preparation import (
    PreparationOutcome,
    prepare_database,
)
from app.infrastructure.persistence.sqlalchemy.base import Base
from app.infrastructure.persistence.sqlalchemy.models import AssetModel

HEAD = "20260718_0001"
EXPECTED_TABLES = set(Base.metadata.tables)


def explicit_settings(target: Path) -> Settings:
    """Configure one explicit temporary SQLite file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{target.as_posix()}",
        database_path=target.parent / "unused-default.db",
        database_directory=target.parent,
    )


def default_settings(target: Path) -> Settings:
    """Configure a default-derived URL so bounded legacy discovery is eligible."""
    target.parent.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        database_path=target,
        database_directory=target.parent,
        data_directory=target.parent.parent,
    )
    assert "database_url" not in settings.model_fields_set
    return settings


def fingerprint(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    return (
        stat.st_size,
        stat.st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def assert_valid_head_database(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_heads() == (HEAD,)
            assert set(inspect(connection).get_table_names()) - {"alembic_version"} == (
                EXPECTED_TABLES
            )
            comparison = MigrationContext.configure(
                connection,
                opts={"compare_type": True},
            )
            assert compare_metadata(comparison, Base.metadata) == []
            assert connection.exec_driver_sql("PRAGMA integrity_check").all() == [("ok",)]
    finally:
        engine.dispose()


def create_unversioned_schema(database: Path, *, symbol: str | None = None) -> None:
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    if symbol is not None:
        with Session(engine) as session:
            session.add(
                AssetModel(
                    id=uuid4(),
                    symbol=symbol,
                    name=f"{symbol} Asset",
                    asset_type="equity",
                    currency="TRY",
                    is_active=True,
                    created_at=datetime(2026, 7, 30, tzinfo=UTC),
                )
            )
            session.commit()
    engine.dispose()


def asset_symbols(database_url: str) -> list[str]:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            return list(session.scalars(select(AssetModel.symbol).order_by(AssetModel.symbol)))
    finally:
        engine.dispose()


def staging_artifacts(directory: Path) -> list[Path]:
    return [
        path for path in directory.iterdir() if ".staging" in path.name or ".rollback" in path.name
    ]


def test_clean_first_run_uses_alembic_without_create_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "runtime" / "clean.db"
    settings = explicit_settings(target)

    def reject_create_all(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("create_all must not run during database preparation")

    monkeypatch.setattr(Base.metadata, "create_all", reject_create_all)

    result = prepare_database(settings, legacy_search_directory=tmp_path)

    assert result.outcome is PreparationOutcome.CREATED
    assert_valid_head_database(settings.database_url)


def test_clean_first_run_reaches_head_and_matches_metadata(tmp_path: Path) -> None:
    target = tmp_path / "runtime" / "head.db"
    settings = explicit_settings(target)

    prepare_database(settings, legacy_search_directory=tmp_path)

    assert current_revision(settings.database_url) == HEAD
    assert_valid_head_database(settings.database_url)


def test_repeated_preparation_is_idempotent(tmp_path: Path) -> None:
    settings = explicit_settings(tmp_path / "runtime" / "repeat.db")

    first = prepare_database(settings, legacy_search_directory=tmp_path)
    second = prepare_database(settings, legacy_search_directory=tmp_path)

    assert first.outcome is PreparationOutcome.CREATED
    assert second.outcome is PreparationOutcome.ALREADY_CURRENT
    assert_valid_head_database(settings.database_url)


def test_current_versioned_database_is_reported_already_current(tmp_path: Path) -> None:
    settings = explicit_settings(tmp_path / "runtime" / "current.db")
    prepare_database(settings, legacy_search_directory=tmp_path)

    result = prepare_database(settings, legacy_search_directory=tmp_path)

    assert result.outcome is PreparationOutcome.ALREADY_CURRENT


def test_available_older_revision_upgrades_to_injected_head(tmp_path: Path) -> None:
    target = tmp_path / "runtime" / "older.db"
    settings = explicit_settings(target)
    prepare_database(settings, legacy_search_directory=tmp_path)
    migration_copy = tmp_path / "migration-copy"
    shutil.copytree(DEFAULT_MIGRATION_SCRIPT_LOCATION, migration_copy)
    (migration_copy / "versions" / "20260730_0002_noop.py").write_text(
        "\n".join(
            (
                '"""Test-only no-op migration."""',
                "revision = '20260730_0002'",
                "down_revision = '20260718_0001'",
                "branch_labels = None",
                "depends_on = None",
                "",
                "def upgrade():",
                "    pass",
                "",
                "def downgrade():",
                "    pass",
                "",
            )
        ),
        encoding="utf-8",
    )

    result = prepare_database(
        settings,
        legacy_search_directory=tmp_path,
        script_location=migration_copy,
    )

    assert result.outcome is PreparationOutcome.UPGRADED
    assert current_revision(settings.database_url) == "20260730_0002"


def test_unknown_alembic_revision_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "runtime" / "unknown.db"
    settings = explicit_settings(target)
    create_unversioned_schema(target)
    with sqlite3.connect(target) as connection:
        connection.execute(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            ("not_in_repository",),
        )

    original = fingerprint(target)
    with pytest.raises(DatabaseError, match="unknown Alembic revision"):
        prepare_database(settings, legacy_search_directory=tmp_path)

    assert fingerprint(target) == original


def test_compatible_unversioned_target_is_stamped_and_rows_survive(tmp_path: Path) -> None:
    target = tmp_path / "runtime" / "unversioned.db"
    settings = explicit_settings(target)
    create_unversioned_schema(target, symbol="STAMPED")

    result = prepare_database(settings, legacy_search_directory=tmp_path)

    assert result.outcome is PreparationOutcome.STAMPED_LEGACY
    assert current_revision(settings.database_url) == HEAD
    assert asset_symbols(settings.database_url) == ["STAMPED"]


def test_incompatible_unversioned_schema_is_rejected_without_replacement(
    tmp_path: Path,
) -> None:
    target = tmp_path / "runtime" / "incompatible.db"
    settings = explicit_settings(target)
    create_unversioned_schema(target)
    with sqlite3.connect(target) as connection:
        connection.execute("ALTER TABLE assets ADD COLUMN guessed_value TEXT")
    original = fingerprint(target)

    with pytest.raises(DatabaseError, match="schema is incompatible"):
        prepare_database(settings, legacy_search_directory=tmp_path)

    assert fingerprint(target) == original


def test_valid_legacy_portfolio_is_copied_without_source_changes(tmp_path: Path) -> None:
    search = tmp_path / "legacy"
    search.mkdir()
    source = search / "portfolio.db"
    target = tmp_path / "runtime" / "portfolio.db"
    create_unversioned_schema(source, symbol="IMPORTED")
    original = fingerprint(source)

    settings = default_settings(target)
    result = prepare_database(settings, legacy_search_directory=search)

    assert result.outcome is PreparationOutcome.IMPORTED_LEGACY
    assert fingerprint(source) == original
    assert asset_symbols(settings.database_url) == ["IMPORTED"]
    assert current_revision(settings.database_url) == HEAD


def test_committed_wal_data_is_imported_without_changing_source(tmp_path: Path) -> None:
    search = tmp_path / "legacy"
    search.mkdir()
    source = search / "portfolio.db"
    target = tmp_path / "runtime" / "portfolio.db"
    create_unversioned_schema(source)
    source_connection = sqlite3.connect(source)
    try:
        assert source_connection.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        source_connection.execute(
            "INSERT INTO assets "
            "(id, symbol, name, asset_type, currency, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                "WALDATA",
                "Committed WAL Asset",
                "equity",
                "TRY",
                1,
                "2026-07-30T00:00:00.000000+00:00",
            ),
        )
        source_connection.commit()
        original = fingerprint(source)

        settings = default_settings(target)
        result = prepare_database(settings, legacy_search_directory=search)

        assert result.outcome is PreparationOutcome.IMPORTED_LEGACY
        assert fingerprint(source) == original
        assert asset_symbols(settings.database_url) == ["WALDATA"]
    finally:
        source_connection.close()


def test_zero_byte_candidate_is_ignored_when_other_candidate_is_valid(
    tmp_path: Path,
) -> None:
    search = tmp_path / "legacy"
    search.mkdir()
    (search / "mira_portfolio.db").touch()
    create_unversioned_schema(search / "portfolio.db", symbol="ONLYVALID")
    settings = default_settings(tmp_path / "runtime" / "portfolio.db")

    result = prepare_database(settings, legacy_search_directory=search)

    assert result.outcome is PreparationOutcome.IMPORTED_LEGACY
    assert asset_symbols(settings.database_url) == ["ONLYVALID"]


def test_two_valid_legacy_candidates_are_rejected_as_ambiguous(tmp_path: Path) -> None:
    search = tmp_path / "legacy"
    search.mkdir()
    create_unversioned_schema(search / "portfolio.db")
    create_unversioned_schema(search / "mira_portfolio.db")
    settings = default_settings(tmp_path / "runtime" / "portfolio.db")

    with pytest.raises(DatabaseError, match="Multiple valid legacy databases"):
        prepare_database(settings, legacy_search_directory=search)

    assert not settings.database_path.exists()


def test_non_empty_corrupt_known_candidate_aborts_preparation(tmp_path: Path) -> None:
    search = tmp_path / "legacy"
    search.mkdir()
    corrupt = search / "portfolio.db"
    corrupt.write_bytes(b"not a sqlite database")
    original = fingerprint(corrupt)
    settings = default_settings(tmp_path / "runtime" / "portfolio.db")

    with pytest.raises(DatabaseError, match="candidate is invalid"):
        prepare_database(settings, legacy_search_directory=search)

    assert fingerprint(corrupt) == original
    assert not settings.database_path.exists()


def test_non_regular_known_candidate_fails_closed(tmp_path: Path) -> None:
    search = tmp_path / "legacy"
    search.mkdir()
    (search / "portfolio.db").mkdir()
    settings = default_settings(tmp_path / "runtime" / "portfolio.db")

    with pytest.raises(DatabaseError, match="not a regular file"):
        prepare_database(settings, legacy_search_directory=search)


def test_existing_target_prevents_legacy_scanning(tmp_path: Path) -> None:
    target = tmp_path / "runtime" / "portfolio.db"
    explicit = explicit_settings(target)
    prepare_database(explicit, legacy_search_directory=tmp_path)
    search = tmp_path / "legacy"
    search.mkdir()
    (search / "portfolio.db").write_bytes(b"corrupt but irrelevant")
    settings = default_settings(target)

    result = prepare_database(settings, legacy_search_directory=search)

    assert result.outcome is PreparationOutcome.ALREADY_CURRENT


def test_explicit_database_url_disables_legacy_discovery(tmp_path: Path) -> None:
    search = tmp_path / "legacy"
    search.mkdir()
    create_unversioned_schema(search / "portfolio.db", symbol="DO_NOT_IMPORT")
    target = tmp_path / "explicit" / "actual.db"
    settings = explicit_settings(target)

    result = prepare_database(settings, legacy_search_directory=search)

    assert result.outcome is PreparationOutcome.CREATED
    assert asset_symbols(settings.database_url) == []
    assert target.exists()
    assert not settings.database_path.exists()


def test_database_url_environment_override_disables_legacy_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = tmp_path / "legacy"
    search.mkdir()
    (search / "portfolio.db").write_bytes(b"corrupt but irrelevant")
    target = tmp_path / "environment" / "actual.db"
    target.parent.mkdir()
    database_url = f"sqlite:///{target.as_posix()}"
    monkeypatch.setenv("MIRA_DATABASE_URL", database_url)
    settings = Settings(
        _env_file=None,
        database_path=target,
        database_directory=target.parent,
    )

    result = prepare_database(settings, legacy_search_directory=search)

    assert result.outcome is PreparationOutcome.CREATED
    assert "database_url" in settings.model_fields_set
    assert target.exists()


def test_migration_failure_leaves_existing_target_byte_for_byte_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "runtime" / "failure.db"
    settings = explicit_settings(target)
    prepare_database(settings, legacy_search_directory=tmp_path)
    original = fingerprint(target)

    def fail_upgrade(_config: object, _revision: str) -> None:
        raise RuntimeError("test migration failure")

    monkeypatch.setattr(alembic_support.command, "upgrade", fail_upgrade)

    with pytest.raises(DatabaseError, match="Staging database"):
        prepare_database(settings, legacy_search_directory=tmp_path)

    assert fingerprint(target) == original
    assert staging_artifacts(target.parent) == []


def test_atomic_install_failure_restores_existing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "runtime" / "install-failure.db"
    settings = explicit_settings(target)
    prepare_database(settings, legacy_search_directory=tmp_path)
    original = fingerprint(target)
    real_replace = database_preparation.os.replace

    def fail_staging_install(source: Path, destination: Path) -> None:
        if ".staging" in Path(source).name and Path(destination) == target:
            raise OSError("injected atomic install failure")
        real_replace(source, destination)

    monkeypatch.setattr(database_preparation.os, "replace", fail_staging_install)

    with pytest.raises(DatabaseError, match="Database preparation failed"):
        prepare_database(settings, legacy_search_directory=tmp_path)

    assert fingerprint(target) == original
    assert staging_artifacts(target.parent) == []


def test_failed_clean_preparation_leaves_no_target_or_staging_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "runtime" / "clean-failure.db"
    settings = explicit_settings(target)

    def fail_upgrade(_config: object, _revision: str) -> None:
        raise RuntimeError("test clean migration failure")

    monkeypatch.setattr(alembic_support.command, "upgrade", fail_upgrade)

    with pytest.raises(DatabaseError, match="Staging database"):
        prepare_database(settings, legacy_search_directory=tmp_path)

    assert not target.exists()
    assert staging_artifacts(target.parent) == []
    assert not any(
        path.name.endswith(("-wal", "-shm", "-journal")) for path in target.parent.iterdir()
    )


def test_failed_preparation_removes_generated_staging_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "runtime" / "sidecar-failure.db"
    settings = explicit_settings(target)

    def fail_with_sidecars(
        database_url: str,
        _config: object,
        _head: str,
    ) -> PreparationOutcome:
        database = Path(str(create_engine(database_url).url.database))
        database.write_bytes(b"partial")
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{database}{suffix}").write_bytes(b"sidecar")
        raise DatabaseError("injected staging failure")

    monkeypatch.setattr(
        database_preparation,
        "_prepare_sqlite_staging",
        fail_with_sidecars,
    )

    with pytest.raises(DatabaseError, match="injected staging failure"):
        prepare_database(settings, legacy_search_directory=tmp_path)

    assert not target.exists()
    assert staging_artifacts(target.parent) == []
    assert list(target.parent.iterdir()) == []


def test_alembic_config_is_cwd_independent_and_script_location_is_injectable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "database.db"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = create_alembic_config(f"sqlite:///{target.as_posix()}")
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == [HEAD]
    assert Path(config.get_main_option("script_location")) == (
        DEFAULT_MIGRATION_SCRIPT_LOCATION.resolve()
    )


def test_multiple_migration_heads_are_rejected_before_target_creation(
    tmp_path: Path,
) -> None:
    migration_copy = tmp_path / "multiple-heads"
    shutil.copytree(DEFAULT_MIGRATION_SCRIPT_LOCATION, migration_copy)
    (migration_copy / "versions" / "independent_head.py").write_text(
        "\n".join(
            (
                '"""Test-only independent head."""',
                "revision = 'independent_head'",
                "down_revision = None",
                "branch_labels = None",
                "depends_on = None",
                "",
                "def upgrade():",
                "    pass",
                "",
                "def downgrade():",
                "    pass",
                "",
            )
        ),
        encoding="utf-8",
    )
    target = tmp_path / "runtime" / "multiple-heads.db"
    settings = explicit_settings(target)

    with pytest.raises(DatabaseError, match="exactly one head"):
        prepare_database(
            settings,
            legacy_search_directory=tmp_path,
            script_location=migration_copy,
        )

    assert not target.exists()
    assert staging_artifacts(target.parent) == []


def test_non_sqlite_override_is_used_exactly_without_legacy_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = tmp_path / "legacy"
    search.mkdir()
    (search / "portfolio.db").write_bytes(b"corrupt but irrelevant")
    database_url = "postgresql+psycopg://mira:secret@db.example/mira?sslmode=require"
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        database_path=tmp_path / "unused.db",
    )
    calls: list[tuple[str, Path | None]] = []

    def record_non_sqlite(url: str, script_location: Path | None) -> PreparationOutcome:
        calls.append((url, script_location))
        return PreparationOutcome.ALREADY_CURRENT

    monkeypatch.setattr(
        database_preparation,
        "_prepare_non_sqlite",
        record_non_sqlite,
    )

    result = prepare_database(settings, legacy_search_directory=search)

    assert result.outcome is PreparationOutcome.ALREADY_CURRENT
    assert calls == [(database_url, None)]
    assert not settings.database_path.exists()


def test_external_connection_alembic_path_uses_exact_supplied_connection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "supplied.db"
    decoy = tmp_path / "must-not-exist.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with engine.connect() as connection:
            config = create_alembic_config(f"sqlite:///{decoy.as_posix()}")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert MigrationContext.configure(connection).get_current_heads() == (HEAD,)
    finally:
        engine.dispose()

    assert database.exists()
    assert not decoy.exists()


def test_cli_style_alembic_upgrade_remains_functional(tmp_path: Path) -> None:
    database = tmp_path / "cli.db"
    config = create_alembic_config(f"sqlite:///{database.as_posix()}")

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    assert current_revision(f"sqlite:///{database.as_posix()}") == HEAD
    assert_valid_head_database(f"sqlite:///{database.as_posix()}")


def test_database_manager_source_contains_no_create_all() -> None:
    source = Path(database_preparation.__file__).parents[1] / "database.py"

    assert "create_all" not in source.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "operation",
    [
        lambda path: path.write_bytes(b"corrupt"),
        lambda path: path.mkdir(),
    ],
)
def test_candidate_failure_never_creates_runtime_target(
    tmp_path: Path,
    operation: Callable[[Path], object],
) -> None:
    search = tmp_path / "legacy"
    search.mkdir()
    operation(search / "mira_portfolio.db")
    settings = default_settings(tmp_path / "runtime" / "portfolio.db")

    with pytest.raises(DatabaseError):
        prepare_database(settings, legacy_search_directory=search)

    assert not settings.database_path.exists()
