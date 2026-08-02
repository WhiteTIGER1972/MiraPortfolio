"""Focused tests for clean-profile Windows distribution validation."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest

import scripts.clean_install_validation as clean_install
from scripts.clean_install_validation import (
    EXPECTED_ARCHIVE_NAME,
    ArchiveMetrics,
    BundleMetrics,
    CleanInstallError,
    EnvironmentMetrics,
    FailureCategory,
    ReadOnlyInstallation,
    ValidationReport,
    _require_allowed_processes,
    clean_child_environment,
    cleanup_validation_root,
    copy_bundle,
    create_clean_profile,
    create_deterministic_archive,
    extract_and_verify_archive,
    failed_report,
    inspect_external_dependency_markers,
    manifest_digest,
    require_unchanged,
    sanitized_process_names,
    serialize_report,
    verify_clean_profile,
    write_report,
)
from scripts.verify_windows_bundle import EXPECTED_TABLES, fingerprint_tree

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = PROJECT_ROOT / "scripts" / "clean_install_validation.py"
VALIDATOR_SCRIPT = PROJECT_ROOT / "scripts" / "validate_clean_install.ps1"
INTERNAL_ALPHA_NOTE = PROJECT_ROOT / "docs" / "internal-alpha-testing.md"


def _minimal_bundle(root: Path) -> Path:
    bundle = root / "MiraPortfolio"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (bundle / "MiraPortfolio.exe").write_bytes(b"frozen executable")
    (internal / "resource.txt").write_text("bundled resource\n", encoding="utf-8")
    return bundle


def _report() -> ValidationReport:
    return ValidationReport(
        result="passed",
        validation_timestamp_utc="2026-08-02T10:00:00Z",
        failure_category=None,
        bundle=BundleMetrics(2, 25, "a" * 64, "b" * 64),
        archive=ArchiveMetrics(EXPECTED_ARCHIVE_NAME, 100, "c" * 64),
        environment=EnvironmentMetrics(
            windows_architecture="AMD64",
            python_available_to_child=False,
            git_available_to_child=False,
            repository_available_to_child=False,
            virtual_environment_available_to_child=False,
            administrator_rights_used=False,
            maximum_profile_path_length=180,
        ),
        scenarios={name: "passed" for name in clean_install.SCENARIO_NAMES},
        observed_processes={name: ("MiraPortfolio.exe",) for name in clean_install.SCENARIO_NAMES},
        read_only_technique=clean_install.READ_ONLY_TECHNIQUE,
        database_revision="20260718_0001",
        database_table_count=7,
    )


def test_clean_environment_uses_allowlist_and_isolated_absolute_paths(
    tmp_path: Path,
) -> None:
    profile = create_clean_profile(tmp_path / "Validation Root Ü With Spaces", "First")
    windows = tmp_path / "Windows"
    (windows / "System32" / "Wbem").mkdir(parents=True)
    source = {
        "SystemRoot": str(windows),
        "WINDIR": str(windows),
        "OS": "Windows_NT",
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "PROCESSOR_ARCHITECTURE": "AMD64",
        "VIRTUAL_ENV": str(PROJECT_ROOT / ".venv"),
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONHOME": "private-python",
        "MIRA_DATABASE_URL": "private-database",
        "GIT_DIR": "private-git",
        "VSCODE_IPC_HOOK": "private-vscode",
        "UNRELATED_SECRET": "private-value",
    }

    environment = clean_child_environment(source, profile)

    assert not any(key.startswith("MIRA_") for key in environment)
    assert (
        not {
            "VIRTUAL_ENV",
            "PYTHONPATH",
            "PYTHONHOME",
            "GIT_DIR",
            "VSCODE_IPC_HOOK",
            "UNRELATED_SECRET",
        }
        & environment.keys()
    )
    assert str(PROJECT_ROOT).casefold() not in environment["PATH"].casefold()
    assert ".venv" not in environment["PATH"].casefold()
    assert "python" not in environment["PATH"].casefold()
    assert environment["PATH"].split(os.pathsep) == [
        str(windows / "System32"),
        str(windows),
        str(windows / "System32" / "Wbem"),
    ]
    for key in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        assert Path(environment[key]).is_absolute()
        assert Path(environment[key]).is_relative_to(profile.root)


def test_clean_profile_supports_spaces_unicode_and_representative_length(
    tmp_path: Path,
) -> None:
    profile = create_clean_profile(tmp_path, "Relocated Launch Ü")

    assert " " in str(profile.database)
    assert "Ü" in str(profile.database)
    assert len(str(profile.database)) >= 100
    assert profile.working_directory.is_dir()
    assert profile.database.is_relative_to(profile.root)


def test_manifest_copy_is_deterministic_and_detects_mutation(tmp_path: Path) -> None:
    source = _minimal_bundle(tmp_path / "source")
    destination = tmp_path / "Staging With Spaces Ü" / "MiraPortfolio"

    staged = copy_bundle(source, destination)

    assert staged == fingerprint_tree(source)
    assert manifest_digest(staged) == manifest_digest(dict(reversed(tuple(staged.items()))))
    require_unchanged(destination, staged)
    (destination / "unexpected.txt").write_text("runtime write", encoding="utf-8")
    with pytest.raises(CleanInstallError) as captured:
        require_unchanged(destination, staged)
    assert captured.value.category is FailureCategory.BUNDLE_MUTATION


@pytest.mark.parametrize(
    ("relative_path", "content"),
    (
        ("developer.txt", str(PROJECT_ROOT)),
        ("__editable___finder.py", ""),
        ("external.pth", r"C:\external\package"),
        ("executable.pth", "import editable_finder"),
    ),
)
def test_static_dependency_scan_rejects_developer_installation_markers(
    tmp_path: Path,
    relative_path: str,
    content: str,
) -> None:
    bundle = _minimal_bundle(tmp_path)
    target = bundle / "_internal" / relative_path
    target.write_text(content, encoding="utf-8")

    with pytest.raises(CleanInstallError) as captured:
        inspect_external_dependency_markers(bundle, PROJECT_ROOT)
    assert captured.value.category is FailureCategory.BUNDLE_CONTENT


@pytest.mark.parametrize("directory", (".git", "tests"))
def test_static_dependency_scan_rejects_repository_only_directories(
    tmp_path: Path,
    directory: str,
) -> None:
    bundle = _minimal_bundle(tmp_path)
    marker = bundle / "_internal" / directory / "marker"
    marker.parent.mkdir()
    marker.write_text("marker", encoding="utf-8")

    with pytest.raises(CleanInstallError):
        inspect_external_dependency_markers(bundle, PROJECT_ROOT)


def test_process_names_are_basenames_sorted_and_bounded() -> None:
    names = sanitized_process_names((r"C:\staging\MiraPortfolio.exe", "helper.exe"))

    assert names == ("helper.exe", "MiraPortfolio.exe")
    assert all("\\" not in name and "/" not in name for name in names)


@pytest.mark.parametrize(
    "name",
    ("python.exe", "pythonw.exe", "py.exe", "pip.exe", "git.exe", "cmd.exe", "powershell.exe"),
)
def test_process_validation_rejects_developer_and_shell_processes(name: str) -> None:
    with pytest.raises(CleanInstallError) as captured:
        _require_allowed_processes(("MiraPortfolio.exe", name))
    assert captured.value.category is FailureCategory.PROCESS


def test_process_validation_accepts_only_the_frozen_application() -> None:
    assert _require_allowed_processes(("MiraPortfolio.exe",)) == ("MiraPortfolio.exe",)

    with pytest.raises(CleanInstallError, match="unapproved"):
        _require_allowed_processes(("MiraPortfolio.exe", "unknown-helper.exe"))


def test_report_is_deterministic_versioned_and_contains_no_sensitive_values() -> None:
    first = serialize_report(_report())
    second = serialize_report(_report())
    document = json.loads(first)

    assert first == second
    assert document["format_version"] == 1
    assert document["failure_category"] is None
    assert document["environment"]["python_available_to_child"] is False
    assert document["environment"]["git_available_to_child"] is False
    assert document["environment"]["administrator_rights_used"] is False
    assert "username" not in first.casefold()
    assert "hostname" not in first.casefold()
    assert "database_url" not in first.casefold()
    assert str(PROJECT_ROOT).casefold() not in first.casefold()
    assert "MIRA_" not in first


def test_explicit_report_is_compact_utf8_and_failure_category_is_explicit(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "reports" / "clean-install.json"
    report = failed_report(FailureCategory.LAUNCH)

    write_report(report, destination)

    raw = destination.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    document = json.loads(raw)
    assert document["result"] == "failed"
    assert document["failure_category"] == "launch"
    assert all(status == "not_run" for status in document["scenarios"].values())


def test_archive_is_deterministic_complete_and_extracts_to_one_folder(
    tmp_path: Path,
) -> None:
    bundle = _minimal_bundle(tmp_path / "bundle-source")
    first_path = tmp_path / "one" / EXPECTED_ARCHIVE_NAME
    second_path = tmp_path / "two" / EXPECTED_ARCHIVE_NAME

    first = create_deterministic_archive(bundle, first_path)
    second = create_deterministic_archive(bundle, second_path)

    assert first.sha256 == second.sha256
    assert first.size_bytes == second.size_bytes
    with zipfile.ZipFile(first_path) as archive:
        names = archive.namelist()
    assert names[0] == "MiraPortfolio/"
    assert "MiraPortfolio/MiraPortfolio.exe" in names
    assert "MiraPortfolio/_internal/" in names
    assert "MiraPortfolio/_internal/resource.txt" in names
    assert not any(
        name.casefold().endswith((".db", ".log", ".env"))
        or "/tests/" in name.casefold()
        or "/.git/" in name.casefold()
        for name in names
    )

    extracted = extract_and_verify_archive(
        first_path,
        tmp_path / "Extracted Alpha Ü",
        fingerprint_tree(bundle),
    )
    assert fingerprint_tree(extracted) == fingerprint_tree(bundle)


def test_clean_profile_verifier_accepts_first_and_second_startup_state(
    tmp_path: Path,
) -> None:
    profile = create_clean_profile(tmp_path, "Database Verification")
    profile.database.parent.mkdir(parents=True)
    expected_head = "20260718_0001"
    with sqlite3.connect(profile.database) as connection:
        for table in EXPECTED_TABLES - {"alembic_version"}:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER)')
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (expected_head,))
    profile.log_directory.mkdir(parents=True)
    (profile.log_directory / "mira-portfolio.log").write_text(
        "Mira Portfolio started\nMira Portfolio started\n",
        encoding="utf-8",
    )

    table_count = verify_clean_profile(
        profile,
        expected_head,
        tmp_path / "repository-not-used",
        expected_start_count=2,
    )

    assert table_count == len(EXPECTED_TABLES)


def test_clean_profile_verifier_rejects_automatic_operational_artifacts(
    tmp_path: Path,
) -> None:
    profile = create_clean_profile(tmp_path, "Automatic Artifact")
    profile.database.parent.mkdir(parents=True)
    expected_head = "20260718_0001"
    with sqlite3.connect(profile.database) as connection:
        for table in EXPECTED_TABLES - {"alembic_version"}:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER)')
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (expected_head,))
    profile.log_directory.mkdir(parents=True)
    (profile.log_directory / "mira-portfolio.log").write_text(
        "Mira Portfolio started\n",
        encoding="utf-8",
    )
    preferences = profile.data_directory / "settings" / "preferences.json"
    preferences.parent.mkdir(parents=True)
    preferences.write_text("{}", encoding="utf-8")

    with pytest.raises(CleanInstallError, match="preference"):
        verify_clean_profile(
            profile,
            expected_head,
            tmp_path / "repository-not-used",
            expected_start_count=1,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL behavior")
def test_read_only_acl_denies_writes_and_restores_after_exception(tmp_path: Path) -> None:
    installation = _minimal_bundle(tmp_path)
    guard = ReadOnlyInstallation(installation)

    with pytest.raises(RuntimeError, match="scenario failure"):
        with guard:
            assert guard.applied
            raise RuntimeError("scenario failure")

    assert guard.restored
    (installation / "write-after-restoration.txt").write_text("restored", encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL behavior")
def test_read_only_acl_restores_when_write_denial_probe_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _minimal_bundle(tmp_path)

    def fail_probe(_: Path) -> None:
        raise CleanInstallError(FailureCategory.READ_ONLY, "probe failed")

    monkeypatch.setattr(clean_install, "_require_installation_write_denied", fail_probe)
    with pytest.raises(CleanInstallError, match="probe failed"):
        with ReadOnlyInstallation(installation):
            pytest.fail("context body must not run")

    (installation / "write-after-enter-failure.txt").write_text("restored", encoding="utf-8")


def test_cleanup_removes_only_owned_system_temp_directories(tmp_path: Path) -> None:
    owned = Path(tempfile.mkdtemp(prefix="Mira Clean Install Ünicode "))
    (owned / "marker.txt").write_text("owned", encoding="utf-8")

    cleanup_validation_root(owned)

    assert not owned.exists()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    with pytest.raises(CleanInstallError) as captured:
        cleanup_validation_root(unrelated)
    assert captured.value.category is FailureCategory.CLEANUP
    assert unrelated.is_dir()


def test_validator_source_has_bounded_pid_scoped_graceful_launch_policy() -> None:
    source = VALIDATOR.read_text(encoding="utf-8")

    assert "time.monotonic() + launch_timeout_seconds" in source
    assert "time.monotonic() + shutdown_timeout_seconds" in source
    assert "observe_process_tree(process.pid)" in source
    assert "_visible_windows_for_processes(observation.process_ids)" in source
    assert "_post_close(main_window)" in source
    assert "_cleanup_failed_process(process)" in source
    assert "process.returncode != 0" in source
    assert "shutil.rmtree(resolved)" in source
    assert "not preserve_diagnostics and permissions_restored" in source


def test_orchestration_script_reuses_build_and_enforces_generated_artifact_policy() -> None:
    source = VALIDATOR_SCRIPT.read_text(encoding="utf-8")
    ignore_rules = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "OSVersion.Platform" in source
    assert '".venv\\Scripts\\python.exe"' in source
    assert '"64bit"' in source
    assert '"scripts.clean_install_validation"' in source
    assert '"build_windows.ps1"' in source
    assert "--preserve-diagnostics" in source
    assert "--report-path" in source
    assert "MiraPortfolio-0.1.0-internal-alpha-win64.zip" in source
    assert "-m PyInstaller" not in source
    assert "pip install" not in source.casefold()
    assert "artifacts/" in ignore_rules


def test_internal_alpha_note_is_complete_and_contains_no_developer_path() -> None:
    source = INTERNAL_ALPHA_NOTE.read_text(encoding="utf-8")
    lowered = source.casefold()

    for required in (
        "internal pre-release",
        "64-bit windows",
        "extract the entire zip",
        "do not run",
        "_internal",
        "miraPortfolio.exe".casefold(),
        "settings & recovery",
        "create backup",
        "stage selected backup",
        "cancel pending restore",
        "next startup",
        "create support bundle",
        "does not automatically\nupload".replace("\n", " "),
        "does not automatically\ncheck".replace("\n", " "),
        "not code-signed",
        "security warning",
        "incident reference",
        "dark theme only",
        "takes effect after restart",
        "no installer",
        "no automatic updater",
        "no automatic backup scheduling",
    ):
        assert required in lowered.replace("\n", " ")
    assert str(PROJECT_ROOT).casefold() not in lowered
    assert "git " not in lowered
    assert "database_url" not in lowered
