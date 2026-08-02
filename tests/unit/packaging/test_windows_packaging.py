"""Focused policy tests for the tracked Windows packaging configuration."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
SPEC = PROJECT_ROOT / "packaging" / "windows" / "MiraPortfolio.spec"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_windows.ps1"
VERIFIER = PROJECT_ROOT / "scripts" / "verify_windows_bundle.py"
CLEAN_VALIDATOR = PROJECT_ROOT / "scripts" / "clean_install_validation.py"
CLEAN_VALIDATOR_SCRIPT = PROJECT_ROOT / "scripts" / "validate_clean_install.ps1"


def test_packaging_extra_pins_pyinstaller_without_runtime_dependency() -> None:
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    assert document["project"]["optional-dependencies"]["packaging"] == ["pyinstaller==6.21.0"]
    assert all(
        not dependency.casefold().startswith("pyinstaller")
        for dependency in document["project"]["dependencies"]
    )


def test_spec_is_tracked_onefolder_windowed_and_narrow() -> None:
    source = SPEC.read_text(encoding="utf-8")
    ignore_rules = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert SPEC.is_file()
    assert "COLLECT(" in source
    assert 'name="MiraPortfolio"' in source
    assert "console=False" in source
    assert "debug=False" in source
    assert "upx=False" in source
    assert 'contents_directory="_internal"' in source
    assert 'PROJECT_ROOT / "app" / "__main__.py"' in source
    assert 'PROJECT_ROOT / "alembic.ini"' in source
    assert 'MIGRATIONS_DIRECTORY.rglob("*")' in source
    assert "hiddenimports=[]" in source
    assert "collect_all" not in source
    assert "uac_admin" not in source
    assert "icon=" not in source
    assert "onefile" not in source.casefold()
    assert "*.spec" in ignore_rules
    assert "!packaging/windows/MiraPortfolio.spec" in ignore_rules


def test_spec_does_not_embed_runtime_or_repository_content() -> None:
    source = SPEC.read_text(encoding="utf-8")

    assert "tests" not in source
    assert ".env" not in source
    assert not re.search(r"[A-Za-z]:[\\/]", source)
    assert not any(extension in source for extension in (".db", ".sqlite", ".mirabackup"))


def test_build_script_enforces_toolchain_and_owned_output_policy() -> None:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "OSVersion.Platform" in source
    assert '".venv\\Scripts\\python.exe"' in source
    assert '"64bit"' in source
    assert '"6.21.0"' in source
    assert "Remove-OwnedBuildDirectory" in source
    assert "FileAttributes]::ReparsePoint" in source
    assert "--noconfirm" in source
    assert "--clean" in source
    assert "MiraPortfolio.spec" in source
    assert "MiraPortfolio.exe" in source
    assert "-m PyInstaller" in source
    assert "pip install" not in source.casefold()
    assert "compress-archive" not in source.casefold()
    assert "onefile" not in source.casefold()
    assert ".msi" not in source.casefold()


def test_packaging_configuration_contains_no_developer_absolute_path() -> None:
    developer_path = str(PROJECT_ROOT).casefold()

    for path in (SPEC, BUILD_SCRIPT, VERIFIER, CLEAN_VALIDATOR, CLEAN_VALIDATOR_SCRIPT):
        assert developer_path not in path.read_text(encoding="utf-8").casefold()
