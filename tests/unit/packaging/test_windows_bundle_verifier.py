"""Focused structural and isolation tests for the Windows bundle verifier."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from scripts.verify_windows_bundle import (
    EXPECTED_TABLES,
    VerificationError,
    _require_unchanged,
    create_runtime_layout,
    expected_revision_files,
    fingerprint_tree,
    isolated_child_environment,
    validate_static_bundle,
    verify_database,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _write_gui_pe(path: Path) -> None:
    header = bytearray(512)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x80)
    header[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", header, 0x80 + 4 + 20 + 68, 2)
    path.write_bytes(header)


@pytest.fixture
def static_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "MiraPortfolio"
    internal = bundle / "_internal"
    versions = internal / "migrations" / "versions"
    plugins = internal / "PySide6" / "plugins" / "platforms"
    versions.mkdir(parents=True)
    plugins.mkdir(parents=True)
    _write_gui_pe(bundle / "MiraPortfolio.exe")
    (internal / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (internal / "migrations" / "env.py").write_text("", encoding="utf-8")
    (internal / "migrations" / "script.py.mako").write_text("", encoding="utf-8")
    for revision in expected_revision_files(PROJECT_ROOT):
        (versions / revision).write_text("revision = 'test'\n", encoding="utf-8")
    (plugins / "qwindows.dll").write_bytes(b"plugin")
    return bundle


def test_static_verifier_accepts_required_bundle_shape(static_bundle: Path) -> None:
    layout = validate_static_bundle(static_bundle, PROJECT_ROOT)

    assert layout.executable.name == "MiraPortfolio.exe"
    assert layout.internal.name == "_internal"
    assert layout.qwindows_plugin.name == "qwindows.dll"


def test_static_verifier_rejects_missing_executable(tmp_path: Path) -> None:
    bundle = tmp_path / "MiraPortfolio"
    bundle.mkdir()

    with pytest.raises(VerificationError, match="MiraPortfolio.exe"):
        validate_static_bundle(bundle, PROJECT_ROOT)


def test_static_verifier_rejects_missing_migration(static_bundle: Path) -> None:
    revision = expected_revision_files(PROJECT_ROOT)[0]
    (static_bundle / "_internal" / "migrations" / "versions" / revision).unlink()

    with pytest.raises(VerificationError, match="revision"):
        validate_static_bundle(static_bundle, PROJECT_ROOT)


def test_static_verifier_rejects_missing_qt_platform_plugin(static_bundle: Path) -> None:
    (static_bundle / "_internal" / "PySide6" / "plugins" / "platforms" / "qwindows.dll").unlink()

    with pytest.raises(VerificationError, match="Qt Windows platform plugin"):
        validate_static_bundle(static_bundle, PROJECT_ROOT)


@pytest.mark.parametrize("forbidden_name", (".env", "embedded.db", "preferences.json"))
def test_static_verifier_rejects_embedded_runtime_files(
    static_bundle: Path,
    forbidden_name: str,
) -> None:
    (static_bundle / "_internal" / forbidden_name).write_bytes(b"forbidden")

    with pytest.raises(VerificationError):
        validate_static_bundle(static_bundle, PROJECT_ROOT)


def test_isolated_environment_removes_inherited_mira_and_qt_values(tmp_path: Path) -> None:
    runtime = create_runtime_layout(tmp_path / "Runtime Root Ü With Spaces")
    source = {
        "SystemRoot": r"C:\Windows",
        "PATH": r"C:\Windows\System32",
        "MIRA_DATABASE_URL": "postgresql://real-secret",
        "MIRA_LOG_LEVEL": "TRACE",
        "QT_QPA_PLATFORM": "offscreen",
        "UNRELATED_SECRET": "private",
    }

    environment = isolated_child_environment(source, runtime)

    assert environment["MIRA_DATABASE_PATH"] == str(runtime.database)
    assert environment["MIRA_DATABASE_URL"].startswith("sqlite:///")
    assert "MIRA_LOG_LEVEL" not in environment
    assert "QT_QPA_PLATFORM" not in environment
    assert "UNRELATED_SECRET" not in environment
    assert environment["APPDATA"] == str(runtime.appdata_directory)
    assert environment["LOCALAPPDATA"] == str(runtime.local_appdata_directory)


def test_database_verifier_requires_revision_schema_and_no_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "verified.db"
    with sqlite3.connect(database) as connection:
        for table in EXPECTED_TABLES - {"alembic_version"}:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER)')
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('current-head')")

    result = verify_database(database, "current-head")

    assert result.revision == "current-head"
    assert EXPECTED_TABLES.issubset(result.tables)


def test_bundle_fingerprint_detects_writes(static_bundle: Path) -> None:
    fingerprint = fingerprint_tree(static_bundle)
    (static_bundle / "unexpected-runtime-write.txt").write_text("changed", encoding="utf-8")

    with pytest.raises(VerificationError, match="modified"):
        _require_unchanged(static_bundle, fingerprint)


def test_verifier_source_uses_finite_graceful_windows_shutdown() -> None:
    source = (PROJECT_ROOT / "scripts" / "verify_windows_bundle.py").read_text(encoding="utf-8")

    assert "time.monotonic() + timeout_seconds" in source
    assert "CreateToolhelp32Snapshot" in source
    assert "th32ParentProcessID" in source
    assert "PostMessageW(window, WM_CLOSE" in source
    assert "process.wait(timeout=shutdown_timeout_seconds)" in source
    assert "process.kill()" in source
    assert "QT_QPA_PLATFORM=offscreen" not in source
