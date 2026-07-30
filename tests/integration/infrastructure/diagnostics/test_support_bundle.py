"""Integration tests for privacy-safe support bundle creation and collection."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import re
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from loguru import logger

from app.core import config, runtime_paths
from app.core.exceptions import SupportBundleCreationError
from app.core.logging import configure_logging
from app.core.settings import Settings
from app.infrastructure.database import DatabaseManager
from app.infrastructure.diagnostics import (
    SupportBundleDiagnosticsService,
    verify_support_bundle,
)
from app.infrastructure.persistence.database_backup import SQLiteBackupService
from app.infrastructure.persistence.database_preparation import prepare_database

CREATED_AT = datetime(2026, 7, 30, 18, 25, tzinfo=UTC)
BUNDLE_ID = UUID("12345678-1234-4234-8234-123456789abc")
CORE_MEMBERS = {
    "manifest.json",
    "application.json",
    "system.json",
    "database.json",
    "runtime.json",
}


@pytest.fixture(autouse=True)
def close_loguru_sinks() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


@pytest.fixture
def diagnostics_settings(tmp_path: Path) -> Settings:
    root = tmp_path / "diagnostics"
    data = root / "data"
    database_directory = data / "database"
    database_path = database_directory / "portfolio.db"
    settings = Settings(
        _env_file=None,
        data_directory=data,
        cache_directory=root / "cache",
        database_directory=database_directory,
        export_directory=data / "exports",
        backup_directory=data / "backups",
        log_directory=root / "logs",
        database_path=database_path,
        database_url=runtime_paths.sqlite_url_for_path(database_path),
    )
    for directory in (
        settings.data_directory,
        settings.cache_directory,
        settings.database_directory,
        settings.export_directory,
        settings.backup_directory,
        settings.log_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    prepare_database(settings, legacy_search_directory=root)
    return settings


@pytest.fixture
def diagnostics_manager(
    diagnostics_settings: Settings,
) -> Iterator[DatabaseManager]:
    manager = DatabaseManager(diagnostics_settings).initialize()
    yield manager
    manager.shutdown()


@pytest.fixture
def diagnostics_service(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> SupportBundleDiagnosticsService:
    configure_logging(diagnostics_settings)
    return SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
    )


def archive_content(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def parsed_json(content: dict[str, bytes], name: str) -> dict[str, object]:
    result = json.loads(content[name].decode("utf-8"))
    assert isinstance(result, dict)
    return result


def staging_artifacts(settings: Settings) -> list[Path]:
    support = settings.export_directory / config.SUPPORT_DIRECTORY_NAME
    if not support.exists():
        return []
    return sorted(
        (path for path in support.iterdir() if path.name.startswith(".mira-support-")),
        key=lambda path: path.name,
    )


def test_support_bundle_has_safe_filename_and_exact_permitted_members(
    diagnostics_service: SupportBundleDiagnosticsService,
) -> None:
    record = diagnostics_service.create_support_bundle()
    content = archive_content(record.path)

    assert record.path.suffix == ".mirasupport"
    assert re.fullmatch(
        r"mira-portfolio-support-20260730T182500Z-[0-9a-f]{12}\.mirasupport",
        record.filename,
    )
    assert set(content).issuperset(CORE_MEMBERS)
    assert set(content) - CORE_MEMBERS <= {"logs/log-1.log"}
    assert record.member_count == len(content)
    assert record.archive_size_bytes == record.path.stat().st_size
    assert record.archive_sha256 == hashlib.sha256(record.path.read_bytes()).hexdigest()


def test_manifest_is_strict_deterministic_versioned_and_covers_members(
    diagnostics_service: SupportBundleDiagnosticsService,
) -> None:
    record = diagnostics_service.create_support_bundle()
    content = archive_content(record.path)
    manifest = parsed_json(content, "manifest.json")

    assert set(manifest) == {
        "format_version",
        "bundle_id",
        "application_name",
        "application_version",
        "created_at_utc",
        "members",
    }
    assert manifest["format_version"] == 1
    assert manifest["bundle_id"] == str(BUNDLE_ID)
    assert manifest["created_at_utc"] == "2026-07-30T18:25:00Z"
    assert content["manifest.json"] == json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    entries = manifest["members"]
    assert isinstance(entries, list)
    assert [entry["name"] for entry in entries] == sorted(set(content) - {"manifest.json"})
    for entry in entries:
        assert set(entry) == {"name", "size_bytes", "sha256", "truncated"}
        member = content[entry["name"]]
        assert entry["size_bytes"] == len(member)
        assert entry["sha256"] == hashlib.sha256(member).hexdigest()


def test_application_system_database_and_runtime_diagnostics_are_truthful(
    diagnostics_settings: Settings,
    diagnostics_service: SupportBundleDiagnosticsService,
) -> None:
    content = archive_content(diagnostics_service.create_support_bundle().path)
    application = parsed_json(content, "application.json")
    system = parsed_json(content, "system.json")
    database = parsed_json(content, "database.json")
    runtime = parsed_json(content, "runtime.json")

    assert application == {
        "application_name": diagnostics_settings.app_name,
        "application_version": diagnostics_settings.app_version,
        "environment": diagnostics_settings.environment,
        "debug": diagnostics_settings.debug,
        "theme": diagnostics_settings.theme,
        "language": diagnostics_settings.language,
        "log_level": diagnostics_settings.log_level,
        "default_currency": diagnostics_settings.default_currency,
        "auto_backup": diagnostics_settings.auto_backup,
        "auto_snapshot": diagnostics_settings.auto_snapshot,
    }
    assert "username" not in system
    assert "hostname" not in system
    assert "executable" not in system
    assert set(system["package_versions"]) == {
        "PySide6",
        "SQLAlchemy",
        "Alembic",
        "Pydantic",
        "pydantic-settings",
        "Loguru",
        "platformdirs",
    }
    assert database["backend_family"] == "sqlite"
    assert database["configured_database_filename"] == "portfolio.db"
    assert database["health_check"] is True
    assert database["current_alembic_revision"] == "20260718_0001"
    assert database["expected_alembic_head"] == "20260718_0001"
    assert database["revision_current"] is True
    assert database["schema_compatible"] is True
    assert database["sqlite_integrity"] is True
    assert database["foreign_key_enforcement"] is True
    assert database["journal_mode"] == "wal"
    assert runtime["pending_restore_state"] == "none"
    assert runtime["backup_archive_count"] == 0
    assert runtime["invalid_backup_count"] == 0


def test_bundle_contains_no_database_payload_paths_urls_or_credentials(
    diagnostics_settings: Settings,
    diagnostics_service: SupportBundleDiagnosticsService,
) -> None:
    record = diagnostics_service.create_support_bundle()
    content = archive_content(record.path)
    combined = b"\n".join(content.values()).decode("utf-8")

    assert "database.sqlite" not in content
    assert not any(name.endswith((".db", ".sqlite", ".mirabackup")) for name in content)
    assert str(diagnostics_settings.data_directory) not in combined
    assert diagnostics_settings.database_url not in combined
    assert str(Path.home()) not in combined
    assert str(Path.cwd()) not in combined
    assert getpass.getuser() not in combined
    assert platform.node() not in combined


def test_log_selection_is_nonrecursive_and_uses_only_application_names(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    configure_logging(diagnostics_settings)
    logger.info("ACTIVE_SAFE_EVENT")
    nested = diagnostics_settings.log_directory / "nested"
    nested.mkdir()
    (nested / config.LOG_FILENAME).write_text("NESTED_SECRET", encoding="utf-8")
    (diagnostics_settings.log_directory / "unrelated.log").write_text(
        "UNRELATED_SECRET",
        encoding="utf-8",
    )
    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
    )

    combined = b"\n".join(archive_content(service.create_support_bundle().path).values())

    assert b"ACTIVE_SAFE_EVENT" in combined
    assert b"NESTED_SECRET" not in combined
    assert b"UNRELATED_SECRET" not in combined


def test_symlinked_log_is_omitted_without_following_it(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    outside = diagnostics_settings.data_directory / "outside.log"
    outside.write_text("LINK_TARGET_SECRET", encoding="utf-8")
    linked = diagnostics_settings.log_directory / "mira-portfolio.2026-07-30.log"
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")
    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
    )

    combined = b"\n".join(archive_content(service.create_support_bundle().path).values())

    assert b"LINK_TARGET_SECRET" not in combined


def test_hard_linked_log_is_omitted_without_reading_it(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    outside = diagnostics_settings.data_directory / "hard-link-target.log"
    outside.write_text("HARD_LINK_TARGET_SECRET", encoding="utf-8")
    linked = diagnostics_settings.log_directory / "mira-portfolio.2026-07-30.log"
    try:
        os.link(outside, linked)
    except OSError:
        pytest.skip("Hard-link creation is unavailable.")
    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
    )

    combined = b"\n".join(archive_content(service.create_support_bundle().path).values())

    assert b"HARD_LINK_TARGET_SECRET" not in combined


def test_log_selection_is_bounded_to_five_newest_files(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    for index in range(7):
        path = diagnostics_settings.log_directory / (f"mira-portfolio.2026-07-3{index}.log")
        path.write_text(f"LOG_{index}", encoding="utf-8")
        path.touch()
    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
    )

    content = archive_content(service.create_support_bundle().path)
    logs = [name for name in content if name.startswith("logs/")]
    runtime = parsed_json(content, "runtime.json")

    assert len(logs) == config.SUPPORT_BUNDLE_MAX_LOG_COUNT
    assert runtime["log_omission_count"] == 2


def test_oversized_log_uses_bounded_tail_and_is_marked_truncated(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    active_log = diagnostics_settings.log_directory / config.LOG_FILENAME
    active_log.write_bytes(b"A" * (config.SUPPORT_BUNDLE_MAX_LOG_BYTES + 1024))
    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
    )

    content = archive_content(service.create_support_bundle().path)
    manifest = parsed_json(content, "manifest.json")
    log_entries = [entry for entry in manifest["members"] if entry["name"].startswith("logs/")]

    assert len(content["logs/log-1.log"]) <= config.SUPPORT_BUNDLE_MAX_LOG_BYTES
    assert log_entries == [
        {
            "name": "logs/log-1.log",
            "size_bytes": len(content["logs/log-1.log"]),
            "sha256": hashlib.sha256(content["logs/log-1.log"]).hexdigest(),
            "truncated": True,
        }
    ]


def test_active_log_growth_is_handled_and_reported_as_truncation(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    active_log = diagnostics_settings.log_directory / config.LOG_FILENAME
    active_log.write_text("before\n", encoding="utf-8")

    def grow_log(path: Path) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write("after\n")

    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
        log_read_hook=grow_log,
    )
    content = archive_content(service.create_support_bundle().path)
    manifest = parsed_json(content, "manifest.json")
    log_entry = next(entry for entry in manifest["members"] if entry["name"] == "logs/log-1.log")

    assert log_entry["truncated"] is True


def test_diagnostics_failure_is_sanitized_and_does_not_block_bundle(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    diagnostics_manager.shutdown()
    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
    )

    content = archive_content(service.create_support_bundle().path)
    database = parsed_json(content, "database.json")

    assert database["status"] == "unavailable"
    assert database["error_category"] == "manager_unavailable"
    assert diagnostics_settings.database_url.encode() not in content["database.json"]


def test_runtime_reports_corrupt_pending_restore_without_including_metadata(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    restore = diagnostics_settings.database_directory / "restore"
    restore.mkdir()
    (restore / "pending.json").write_text("{invalid", encoding="utf-8")
    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
    )

    content = archive_content(service.create_support_bundle().path)
    runtime = parsed_json(content, "runtime.json")

    assert runtime["pending_restore_state"] == "corrupt"
    assert b"{invalid" not in b"\n".join(content.values())
    assert "pending.json" not in content


def test_runtime_counts_valid_and_invalid_backups_without_including_them(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    SQLiteBackupService(diagnostics_settings, clock=lambda: CREATED_AT).create_backup()
    (diagnostics_settings.backup_directory / "corrupt.mirabackup").write_bytes(b"invalid")
    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
    )

    content = archive_content(service.create_support_bundle().path)
    runtime = parsed_json(content, "runtime.json")

    assert runtime["backup_archive_count"] == 1
    assert runtime["invalid_backup_count"] == 1
    assert not any(name.endswith(".mirabackup") for name in content)


def test_same_second_bundles_do_not_collide(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    ids = iter(
        (
            UUID("12345678-1234-4234-8234-123456789abc"),
            UUID("abcdefab-1234-4234-8234-123456789abc"),
        )
    )
    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: next(ids),
    )

    first = service.create_support_bundle()
    second = service.create_support_bundle()

    assert first.path != second.path
    assert first.path.exists()
    assert second.path.exists()


def test_existing_bundle_is_never_overwritten(
    diagnostics_service: SupportBundleDiagnosticsService,
) -> None:
    record = diagnostics_service.create_support_bundle()
    original = record.path.read_bytes()

    with pytest.raises(SupportBundleCreationError, match="already exists"):
        diagnostics_service.create_support_bundle()

    assert record.path.read_bytes() == original


def test_support_directory_is_created_only_on_explicit_operation(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
) -> None:
    support = diagnostics_settings.export_directory / config.SUPPORT_DIRECTORY_NAME
    service = SupportBundleDiagnosticsService(diagnostics_settings, diagnostics_manager)

    assert not support.exists()
    service.create_support_bundle()
    assert support.is_dir()


def test_staging_archives_are_removed_after_success_and_failure(
    diagnostics_settings: Settings,
    diagnostics_manager: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SupportBundleDiagnosticsService(
        diagnostics_settings,
        diagnostics_manager,
        clock=lambda: CREATED_AT,
        uuid_factory=lambda: BUNDLE_ID,
    )
    service.create_support_bundle()
    assert staging_artifacts(diagnostics_settings) == []

    from app.infrastructure.diagnostics import support_bundle

    monkeypatch.setattr(
        support_bundle,
        "_write_archive",
        lambda *_arguments: (_ for _ in ()).throw(OSError("write failed")),
    )
    with pytest.raises(SupportBundleCreationError, match="could not be created safely"):
        SupportBundleDiagnosticsService(
            diagnostics_settings,
            diagnostics_manager,
            clock=lambda: CREATED_AT.replace(second=1),
            uuid_factory=lambda: UUID("abcdefab-1234-4234-8234-123456789abc"),
        ).create_support_bundle()
    assert staging_artifacts(diagnostics_settings) == []


def test_independent_verification_returns_the_same_identity(
    diagnostics_settings: Settings,
    diagnostics_service: SupportBundleDiagnosticsService,
) -> None:
    created = diagnostics_service.create_support_bundle()

    verified = verify_support_bundle(diagnostics_settings, created.path)

    assert verified == created
