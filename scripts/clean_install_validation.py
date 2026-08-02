"""Validate a frozen Windows bundle under hardened clean-profile conditions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final
from uuid import uuid4

from scripts.verify_windows_bundle import (
    APP_NAME,
    BACKUP_EXTENSION,
    EXECUTABLE_NAME,
    LOG_FILENAME,
    PREFERENCES_FILENAME,
    SUPPORT_EXTENSION,
    BundleLayout,
    VerificationError,
    _cleanup_failed_process,
    _post_close,
    _visible_windows_for_processes,
    bundled_single_head,
    fingerprint_tree,
    observe_process_tree,
    validate_static_bundle,
    verify_database,
)

sys.dont_write_bytecode = True

REPORT_FORMAT_VERSION: Final = 1
EXPECTED_ARCHIVE_NAME: Final = "MiraPortfolio-0.1.0-internal-alpha-win64.zip"
STRATEGY_NAME: Final = "hardened_clean_profile_process_isolation"
READ_ONLY_TECHNIQUE: Final = "current_sid_deny_write_data_append_acl"
SCENARIO_NAMES: Final = (
    "first_launch",
    "second_launch",
    "relocated_launch",
    "read_only_install",
    "archive_extraction",
)
FORBIDDEN_CHILD_EXECUTABLES: Final = frozenset(
    {
        "cmd.exe",
        "git.exe",
        "pip.exe",
        "powershell.exe",
        "pwsh.exe",
        "py.exe",
        "python.exe",
        "pythonw.exe",
    }
)
ALLOWED_APPLICATION_EXECUTABLES: Final = frozenset({EXECUTABLE_NAME.casefold()})
INHERITED_ENVIRONMENT_ALLOWLIST: Final = frozenset(
    {
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "PROCESSOR_LEVEL",
        "PROCESSOR_REVISION",
        "SYSTEMROOT",
        "WINDIR",
    }
)
TEXT_INSPECTION_SUFFIXES: Final = frozenset({".cfg", ".ini", ".json", ".pth", ".py", ".txt"})
TEXT_INSPECTION_LIMIT_BYTES: Final = 2 * 1024 * 1024
PROCESS_NAME_LIMIT: Final = 64
PROCESS_COUNT_LIMIT: Final = 32
_PROCESS_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")


class FailureCategory(StrEnum):
    """Bounded failure categories safe for machine-readable reports."""

    ARCHIVE = "archive"
    BUNDLE_CONTENT = "bundle_content"
    BUNDLE_MUTATION = "bundle_mutation"
    CLEANUP = "cleanup"
    DATABASE = "database"
    ENVIRONMENT = "environment"
    FILESYSTEM = "filesystem"
    LAUNCH = "launch"
    LOGGING = "logging"
    PROCESS = "process"
    READ_ONLY = "read_only"
    TOOLCHAIN = "toolchain"


class CleanInstallError(RuntimeError):
    """Privacy-safe validation failure with an explicit report category."""

    def __init__(self, category: FailureCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class CleanProfile:
    """One isolated simulated Windows user profile and runtime hierarchy."""

    root: Path
    user_profile: Path
    appdata: Path
    local_appdata: Path
    temporary: Path
    working_directory: Path
    data_directory: Path
    cache_directory: Path
    log_directory: Path
    database: Path
    backup_directory: Path
    export_directory: Path


@dataclass(frozen=True, slots=True)
class BundleMetrics:
    """Content-addressed bundle values safe to publish in a report."""

    file_count: int
    total_size_bytes: int
    executable_sha256: str
    manifest_sha256: str

    def to_document(self) -> dict[str, object]:
        return {
            "executable_sha256": self.executable_sha256,
            "file_count": self.file_count,
            "manifest_sha256": self.manifest_sha256,
            "total_size_bytes": self.total_size_bytes,
        }


@dataclass(frozen=True, slots=True)
class ArchiveMetrics:
    """Content-addressed internal Alpha ZIP values."""

    filename: str
    size_bytes: int
    sha256: str

    def to_document(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class EnvironmentMetrics:
    """Sanitized clean-child environment assertions."""

    windows_architecture: str
    python_available_to_child: bool
    git_available_to_child: bool
    repository_available_to_child: bool
    virtual_environment_available_to_child: bool
    administrator_rights_used: bool
    maximum_profile_path_length: int

    def to_document(self) -> dict[str, object]:
        return {
            "administrator_rights_used": self.administrator_rights_used,
            "git_available_to_child": self.git_available_to_child,
            "maximum_profile_path_length": self.maximum_profile_path_length,
            "python_available_to_child": self.python_available_to_child,
            "repository_available_to_child": self.repository_available_to_child,
            "virtual_environment_available_to_child": (self.virtual_environment_available_to_child),
            "windows_architecture": self.windows_architecture,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Versioned deterministic clean-install validation report."""

    result: str
    validation_timestamp_utc: str
    failure_category: str | None
    bundle: BundleMetrics
    archive: ArchiveMetrics
    environment: EnvironmentMetrics
    scenarios: dict[str, str]
    observed_processes: dict[str, tuple[str, ...]]
    read_only_technique: str
    database_revision: str
    database_table_count: int

    def to_document(self) -> dict[str, object]:
        return {
            "archive": self.archive.to_document(),
            "bundle": self.bundle.to_document(),
            "database": {
                "revision": self.database_revision,
                "table_count": self.database_table_count,
            },
            "environment": self.environment.to_document(),
            "failure_category": self.failure_category,
            "format_version": REPORT_FORMAT_VERSION,
            "observed_processes": {
                name: list(self.observed_processes[name]) for name in SCENARIO_NAMES
            },
            "read_only_technique": self.read_only_technique,
            "result": self.result,
            "scenarios": {name: self.scenarios[name] for name in SCENARIO_NAMES},
            "strategy": STRATEGY_NAME,
            "validation_timestamp_utc": self.validation_timestamp_utc,
        }


def create_clean_profile(root: Path, scenario_name: str) -> CleanProfile:
    """Create an isolated long, spaced, Unicode simulated user profile."""
    profile_root = (
        root
        / "Clean Profiles"
        / scenario_name
        / "Users"
        / "Internal Alpha Tester Ü"
        / "Representative Windows Profile Path 0123456789"
    )
    user_profile = profile_root / "Profile"
    appdata = user_profile / "AppData" / "Roaming"
    local_appdata = user_profile / "AppData" / "Local"
    temporary = local_appdata / "Temp"
    working_directory = root / "Unrelated Working Directories" / scenario_name
    data_directory = local_appdata / "Mira" / "Mira Portfolio"
    profile = CleanProfile(
        root=profile_root,
        user_profile=user_profile,
        appdata=appdata,
        local_appdata=local_appdata,
        temporary=temporary,
        working_directory=working_directory,
        data_directory=data_directory,
        cache_directory=data_directory / "Cache",
        log_directory=data_directory / "Logs",
        database=data_directory / "database" / "portfolio.db",
        backup_directory=data_directory / "backups",
        export_directory=data_directory / "exports",
    )
    for directory in (
        profile.user_profile,
        profile.appdata,
        profile.local_appdata,
        profile.temporary,
        profile.working_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return profile


def clean_child_environment(
    source: Mapping[str, str],
    profile: CleanProfile,
) -> dict[str, str]:
    """Construct a strict Windows allowlist with no inherited developer tooling."""
    normalized = {key.upper(): value for key, value in source.items()}
    system_root_value = normalized.get("SYSTEMROOT") or normalized.get("WINDIR")
    if system_root_value is None:
        raise CleanInstallError(
            FailureCategory.ENVIRONMENT,
            "The Windows system root is unavailable.",
        )
    system_root = Path(system_root_value).resolve()
    system32 = system_root / "System32"
    minimal_path = os.pathsep.join(str(path) for path in (system32, system_root, system32 / "Wbem"))
    environment = {
        key: normalized[key] for key in INHERITED_ENVIRONMENT_ALLOWLIST if key in normalized
    }
    environment.update(
        {
            "APPDATA": str(profile.appdata),
            "COMSPEC": str(system32 / "cmd.exe"),
            "HOME": str(profile.user_profile),
            "LOCALAPPDATA": str(profile.local_appdata),
            "PATH": minimal_path,
            "PATHEXT": normalized.get(
                "PATHEXT",
                ".COM;.EXE;.BAT;.CMD",
            ),
            "SYSTEMDRIVE": system_root.drive,
            "SYSTEMROOT": str(system_root),
            "TEMP": str(profile.temporary),
            "TMP": str(profile.temporary),
            "USERPROFILE": str(profile.user_profile),
            "WINDIR": str(system_root),
        }
    )
    drive, tail = os.path.splitdrive(str(profile.user_profile))
    environment["HOMEDRIVE"] = drive
    environment["HOMEPATH"] = tail
    _require_clean_environment(environment, profile)
    return environment


def _require_clean_environment(
    environment: Mapping[str, str],
    profile: CleanProfile,
) -> None:
    forbidden_prefixes = (
        "CONDA_",
        "GIT_",
        "MIRA_",
        "PIP_",
        "POETRY_",
        "PYINSTALLER_",
        "PYTEST_",
        "PYTHON",
        "UV_",
        "VIRTUAL_ENV",
        "VSCODE_",
    )
    if any(key.upper().startswith(forbidden_prefixes) for key in environment):
        raise CleanInstallError(
            FailureCategory.ENVIRONMENT,
            "A developer environment variable reached the clean child.",
        )
    for key in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        path = Path(environment[key])
        if not path.is_absolute() or not path.resolve().is_relative_to(profile.root.resolve()):
            raise CleanInstallError(
                FailureCategory.ENVIRONMENT,
                "A clean profile path escaped the isolated profile.",
            )
    path_value = environment["PATH"]
    forbidden_fragments = (".venv", "git", "python", "visual studio code", "vscode")
    if any(fragment in path_value.casefold() for fragment in forbidden_fragments):
        raise CleanInstallError(
            FailureCategory.ENVIRONMENT,
            "The child PATH contains developer tooling.",
        )
    if shutil.which("python.exe", path=path_value) is not None:
        raise CleanInstallError(
            FailureCategory.ENVIRONMENT,
            "Python is discoverable through the clean child PATH.",
        )
    if shutil.which("git.exe", path=path_value) is not None:
        raise CleanInstallError(
            FailureCategory.ENVIRONMENT,
            "Git is discoverable through the clean child PATH.",
        )


def manifest_digest(manifest: Mapping[str, tuple[int, str]]) -> str:
    """Hash a deterministic relative-path, size, and content-hash manifest."""
    digest = hashlib.sha256()
    for relative_path in sorted(manifest):
        size, content_hash = manifest[relative_path]
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(content_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def bundle_metrics(layout: BundleLayout) -> BundleMetrics:
    manifest = fingerprint_tree(layout.root)
    executable = manifest.get(EXECUTABLE_NAME)
    if executable is None:
        raise CleanInstallError(
            FailureCategory.BUNDLE_CONTENT,
            "The executable is absent from the bundle manifest.",
        )
    return BundleMetrics(
        file_count=len(manifest),
        total_size_bytes=sum(size for size, _ in manifest.values()),
        executable_sha256=executable[1],
        manifest_sha256=manifest_digest(manifest),
    )


def copy_bundle(source: Path, destination: Path) -> dict[str, tuple[int, str]]:
    """Copy one complete bundle and require byte-identical manifests."""
    if destination.exists() or destination.is_symlink():
        raise CleanInstallError(
            FailureCategory.FILESYSTEM,
            "The owned staging destination already exists.",
        )
    try:
        shutil.copytree(source, destination)
    except OSError as error:
        raise CleanInstallError(
            FailureCategory.FILESYSTEM,
            "The bundle could not be copied into clean staging.",
        ) from error
    source_manifest = fingerprint_tree(source)
    staged_manifest = fingerprint_tree(destination)
    if source_manifest != staged_manifest:
        raise CleanInstallError(
            FailureCategory.BUNDLE_MUTATION,
            "The staged bundle does not match the source manifest.",
        )
    return staged_manifest


def require_unchanged(
    root: Path,
    expected: Mapping[str, tuple[int, str]],
) -> None:
    """Require an exact bundle manifest using a clean-install failure category."""
    if fingerprint_tree(root) != dict(expected):
        raise CleanInstallError(
            FailureCategory.BUNDLE_MUTATION,
            "The installed bundle was modified during validation.",
        )


def inspect_external_dependency_markers(bundle: Path, repository_root: Path) -> None:
    """Reject bounded text/config markers that point outside the frozen bundle."""
    repository_text = str(repository_root.resolve()).casefold()
    repository_posix = repository_text.replace("\\", "/")
    for path in bundle.rglob("*"):
        name = path.name.casefold()
        relative_parts = tuple(part.casefold() for part in path.relative_to(bundle).parts)
        if "__editable__" in name or name.endswith(".egg-info"):
            raise CleanInstallError(
                FailureCategory.BUNDLE_CONTENT,
                "Editable installation metadata was embedded in the bundle.",
            )
        if ".git" in relative_parts or "tests" in relative_parts:
            raise CleanInstallError(
                FailureCategory.BUNDLE_CONTENT,
                "Repository-only content was embedded in the bundle.",
            )
        if not path.is_file() or path.suffix.casefold() not in TEXT_INSPECTION_SUFFIXES:
            continue
        try:
            if path.stat().st_size > TEXT_INSPECTION_LIMIT_BYTES:
                raise CleanInstallError(
                    FailureCategory.BUNDLE_CONTENT,
                    "A bundled configuration file exceeds the inspection limit.",
                )
            text = path.read_text(encoding="utf-8")
        except UnicodeError as error:
            raise CleanInstallError(
                FailureCategory.BUNDLE_CONTENT,
                "A bundled configuration file is not valid UTF-8.",
            ) from error
        lowered = text.casefold()
        if repository_text in lowered or repository_posix in lowered or "__editable__" in lowered:
            raise CleanInstallError(
                FailureCategory.BUNDLE_CONTENT,
                "A bundled configuration file references developer installation state.",
            )
        if path.suffix.casefold() == ".pth":
            _require_internal_pth(path, text, bundle)


def _require_internal_pth(path: Path, text: str, bundle: Path) -> None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("import "):
            raise CleanInstallError(
                FailureCategory.BUNDLE_CONTENT,
                "A bundled path file contains executable installation metadata.",
            )
        candidate = Path(line)
        if candidate.is_absolute() or not (path.parent / candidate).resolve().is_relative_to(
            bundle.resolve()
        ):
            raise CleanInstallError(
                FailureCategory.BUNDLE_CONTENT,
                "A bundled path file references an external location.",
            )


def sanitized_process_names(names: Sequence[str]) -> tuple[str, ...]:
    """Return bounded executable basenames or fail closed."""
    sanitized: set[str] = set()
    for value in names:
        name = Path(value).name
        if len(name) > PROCESS_NAME_LIMIT or _PROCESS_NAME_PATTERN.fullmatch(name) is None:
            raise CleanInstallError(
                FailureCategory.PROCESS,
                "An observed process name is not safe to report.",
            )
        sanitized.add(name)
    if len(sanitized) > PROCESS_COUNT_LIMIT:
        raise CleanInstallError(
            FailureCategory.PROCESS,
            "The application process tree exceeds the reporting limit.",
        )
    return tuple(sorted(sanitized, key=str.casefold))


def _require_allowed_processes(names: Sequence[str]) -> tuple[str, ...]:
    sanitized = sanitized_process_names(names)
    lowered = {name.casefold() for name in sanitized}
    if lowered & FORBIDDEN_CHILD_EXECUTABLES:
        raise CleanInstallError(
            FailureCategory.PROCESS,
            "The application launched a forbidden developer or shell process.",
        )
    if any(name not in ALLOWED_APPLICATION_EXECUTABLES for name in lowered):
        raise CleanInstallError(
            FailureCategory.PROCESS,
            "The application launched an unapproved child process.",
        )
    return sanitized


def launch_clean_application(
    layout: BundleLayout,
    profile: CleanProfile,
    *,
    launch_timeout_seconds: int,
    shutdown_timeout_seconds: int,
) -> tuple[str, ...]:
    """Launch under the clean environment, observe the PID tree, and close normally."""
    environment = clean_child_environment(os.environ, profile)
    try:
        process = subprocess.Popen(
            [str(layout.executable)],
            cwd=profile.working_directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as error:
        raise CleanInstallError(
            FailureCategory.LAUNCH,
            "The clean application process could not be created.",
        ) from error
    observed: set[str] = set()
    try:
        launch_deadline = time.monotonic() + launch_timeout_seconds
        main_window: int | None = None
        while time.monotonic() < launch_deadline:
            if process.poll() is not None:
                raise CleanInstallError(
                    FailureCategory.LAUNCH,
                    "The application exited before showing MainWindow.",
                )
            observation = observe_process_tree(process.pid)
            observed.update(observation.executable_names)
            _require_allowed_processes(tuple(observed))
            windows = _visible_windows_for_processes(observation.process_ids)
            for window, title in windows:
                if title == APP_NAME:
                    main_window = window
                    break
            if main_window is not None:
                break
            if any("unexpected error" in title.casefold() for _, title in windows):
                raise CleanInstallError(
                    FailureCategory.LAUNCH,
                    "A fatal dialog replaced the application MainWindow.",
                )
            time.sleep(0.1)
        if main_window is None:
            raise CleanInstallError(
                FailureCategory.LAUNCH,
                "The application did not show MainWindow before the finite timeout.",
            )

        _post_close(main_window)
        shutdown_deadline = time.monotonic() + shutdown_timeout_seconds
        while process.poll() is None and time.monotonic() < shutdown_deadline:
            observation = observe_process_tree(process.pid)
            observed.update(observation.executable_names)
            _require_allowed_processes(tuple(observed))
            time.sleep(0.1)
        if process.poll() is None:
            raise CleanInstallError(
                FailureCategory.LAUNCH,
                "The application did not shut down after the normal close message.",
            )
        if process.returncode != 0:
            raise CleanInstallError(
                FailureCategory.LAUNCH,
                "The application returned a non-zero exit code.",
            )
        return _require_allowed_processes(tuple(observed))
    except CleanInstallError:
        _cleanup_failed_process(process)
        raise
    except (OSError, VerificationError) as error:
        _cleanup_failed_process(process)
        raise CleanInstallError(
            FailureCategory.LAUNCH,
            "The clean application launch could not be completed.",
        ) from error


def verify_clean_profile(
    profile: CleanProfile,
    expected_head: str,
    repository_root: Path,
    *,
    expected_start_count: int,
) -> int:
    """Verify first-run data, clean shutdown, logs, and absent automatic artifacts."""
    try:
        database = verify_database(profile.database, expected_head)
    except VerificationError as error:
        raise CleanInstallError(
            FailureCategory.DATABASE,
            "The clean-profile database did not pass validation.",
        ) from error
    log_path = profile.log_directory / LOG_FILENAME
    if not log_path.is_file() or log_path.is_symlink():
        raise CleanInstallError(
            FailureCategory.LOGGING,
            "The clean-profile persistent log was not created safely.",
        )
    try:
        log_text = log_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CleanInstallError(
            FailureCategory.LOGGING,
            "The clean-profile persistent log is not valid UTF-8.",
        ) from error
    lowered = log_text.casefold()
    if log_text.count("Mira Portfolio started") < expected_start_count:
        raise CleanInstallError(
            FailureCategory.LOGGING,
            "The persistent log does not prove the expected startup count.",
        )
    if "fatal_unhandled_error" in lowered or "traceback (most recent call last)" in lowered:
        raise CleanInstallError(
            FailureCategory.LOGGING,
            "The persistent log contains a fatal incident or traceback.",
        )
    repository_text = str(repository_root.resolve()).casefold()
    if repository_text in lowered or repository_text.replace("\\", "/") in lowered:
        raise CleanInstallError(
            FailureCategory.LOGGING,
            "The persistent log contains a developer repository path.",
        )
    if "sqlite:" in lowered:
        raise CleanInstallError(
            FailureCategory.LOGGING,
            "The persistent log contains a database URL.",
        )

    if (profile.data_directory / "settings" / PREFERENCES_FILENAME).exists():
        raise CleanInstallError(
            FailureCategory.FILESYSTEM,
            "Startup created an automatic preference file.",
        )
    if (profile.database.parent / "restore").exists():
        raise CleanInstallError(
            FailureCategory.FILESYSTEM,
            "Startup created a restore workspace.",
        )
    support = profile.data_directory / "support"
    if support.exists() and any(support.rglob("*")):
        raise CleanInstallError(
            FailureCategory.FILESYSTEM,
            "Startup created a support artifact.",
        )
    if profile.backup_directory.exists() and any(profile.backup_directory.rglob("*")):
        raise CleanInstallError(
            FailureCategory.FILESYSTEM,
            "Startup created an automatic backup.",
        )
    if any(profile.root.rglob(f"*{BACKUP_EXTENSION}")) or any(
        profile.root.rglob(f"*{SUPPORT_EXTENSION}")
    ):
        raise CleanInstallError(
            FailureCategory.FILESYSTEM,
            "Startup created an unexpected operational archive.",
        )
    if any(profile.working_directory.rglob("*")):
        raise CleanInstallError(
            FailureCategory.FILESYSTEM,
            "The application wrote into its unrelated working directory.",
        )
    try:
        with closing(
            sqlite3.connect(f"{profile.database.as_uri()}?mode=ro", uri=True)
        ) as connection:
            version_count = connection.execute("SELECT COUNT(*) FROM alembic_version").fetchone()
    except sqlite3.Error as error:
        raise CleanInstallError(
            FailureCategory.DATABASE,
            "The clean database could not be reopened read-only.",
        ) from error
    if version_count != (1,):
        raise CleanInstallError(
            FailureCategory.DATABASE,
            "The database contains duplicate schema initialization state.",
        )
    return len(database.tables)


class ReadOnlyInstallation:
    """Apply and always remove an inherited current-user deny-write ACL."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._principal = _current_user_sid_principal()
        system_root = Path(os.environ["SystemRoot"]).resolve()
        self._icacls = system_root / "System32" / "icacls.exe"
        self.applied = False
        self.restored = False

    def __enter__(self) -> ReadOnlyInstallation:
        _run_icacls(
            self._icacls,
            (
                str(self._root),
                "/deny",
                f"{self._principal}:(OI)(CI)(WD,AD)",
                "/T",
                "/C",
            ),
        )
        self.applied = True
        try:
            _require_installation_write_denied(self._root)
        except Exception:
            self._restore()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if not self.applied:
            return
        self._restore()

    def _restore(self) -> None:
        _run_icacls(
            self._icacls,
            (str(self._root), "/remove:d", self._principal, "/T", "/C"),
        )
        self.restored = True


def _current_user_sid_principal() -> str:
    try:
        completed = subprocess.run(
            ("whoami.exe", "/user", "/fo", "csv", "/nh"),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        row = next(csv.reader(io.StringIO(completed.stdout)))
    except (OSError, subprocess.SubprocessError, StopIteration, csv.Error) as error:
        raise CleanInstallError(
            FailureCategory.READ_ONLY,
            "The current Windows identity could not be resolved safely.",
        ) from error
    if len(row) < 2 or re.fullmatch(r"S-\d+(?:-\d+)+", row[1]) is None:
        raise CleanInstallError(
            FailureCategory.READ_ONLY,
            "The current Windows identity returned an invalid SID.",
        )
    return f"*{row[1]}"


def _run_icacls(executable: Path, arguments: Sequence[str]) -> None:
    try:
        completed = subprocess.run(
            (str(executable), *arguments),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CleanInstallError(
            FailureCategory.READ_ONLY,
            "The temporary installation ACL operation could not run.",
        ) from error
    if completed.returncode != 0:
        raise CleanInstallError(
            FailureCategory.READ_ONLY,
            "The temporary installation ACL operation failed.",
        )


def _require_installation_write_denied(root: Path) -> None:
    probe = root / f".write-denial-probe-{uuid4().hex}"
    try:
        probe.write_bytes(b"probe")
    except PermissionError:
        pass
    else:
        probe.unlink(missing_ok=True)
        raise CleanInstallError(
            FailureCategory.READ_ONLY,
            "The installation directory remained writable after ACL protection.",
        )
    existing = root / EXECUTABLE_NAME
    try:
        descriptor = os.open(existing, os.O_WRONLY)
    except PermissionError:
        return
    else:
        os.close(descriptor)
        raise CleanInstallError(
            FailureCategory.READ_ONLY,
            "An installed file remained writable after ACL protection.",
        )


def create_deterministic_archive(bundle: Path, archive_path: Path) -> ArchiveMetrics:
    """Create the internal Alpha archive with fixed ordering and ZIP metadata."""
    if archive_path.name != EXPECTED_ARCHIVE_NAME or archive_path.suffix.casefold() != ".zip":
        raise CleanInstallError(
            FailureCategory.ARCHIVE,
            "The internal Alpha archive target has an unexpected name.",
        )
    if archive_path.is_symlink() or (archive_path.exists() and not archive_path.is_file()):
        raise CleanInstallError(
            FailureCategory.ARCHIVE,
            "The internal Alpha archive target is unsafe.",
        )
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_name(f".{archive_path.name}.{uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            _write_zip_directory(archive, "MiraPortfolio/")
            directories = sorted(path for path in bundle.rglob("*") if path.is_dir())
            for directory in directories:
                relative = directory.relative_to(bundle).as_posix()
                _write_zip_directory(archive, f"MiraPortfolio/{relative}/")
            for source in sorted(path for path in bundle.rglob("*") if path.is_file()):
                relative = source.relative_to(bundle).as_posix()
                info = zipfile.ZipInfo(
                    f"MiraPortfolio/{relative}",
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                with source.open("rb") as input_stream, archive.open(info, mode="w") as output:
                    shutil.copyfileobj(input_stream, output, length=1024 * 1024)
        os.replace(temporary, archive_path)
    except (OSError, zipfile.BadZipFile) as error:
        temporary.unlink(missing_ok=True)
        raise CleanInstallError(
            FailureCategory.ARCHIVE,
            "The internal Alpha archive could not be created safely.",
        ) from error
    digest = _hash_file(archive_path)
    return ArchiveMetrics(archive_path.name, archive_path.stat().st_size, digest)


def _write_zip_directory(archive: zipfile.ZipFile, name: str) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (0o40755 << 16) | 0x10
    archive.writestr(info, b"")


def extract_and_verify_archive(
    archive_path: Path,
    extraction_root: Path,
    expected_manifest: Mapping[str, tuple[int, str]],
) -> Path:
    """Extract the owned archive and require its exact single-folder manifest."""
    try:
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            names = archive.namelist()
            if not names or any(
                Path(name).is_absolute()
                or ".." in Path(name).parts
                or not name.replace("\\", "/").startswith("MiraPortfolio/")
                for name in names
            ):
                raise CleanInstallError(
                    FailureCategory.ARCHIVE,
                    "The internal Alpha archive has an unsafe member layout.",
                )
            archive.extractall(extraction_root)
    except (OSError, zipfile.BadZipFile) as error:
        raise CleanInstallError(
            FailureCategory.ARCHIVE,
            "The internal Alpha archive could not be extracted safely.",
        ) from error
    extracted = extraction_root / "MiraPortfolio"
    if fingerprint_tree(extracted) != dict(expected_manifest):
        raise CleanInstallError(
            FailureCategory.ARCHIVE,
            "The extracted Alpha bundle does not match its source manifest.",
        )
    return extracted


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def serialize_report(report: ValidationReport) -> str:
    """Serialize deterministic compact UTF-8 JSON without non-finite values."""
    return json.dumps(
        report.to_document(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def write_report(report: ValidationReport, destination: Path) -> None:
    """Atomically write one sanitized report to an explicit or owned destination."""
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise CleanInstallError(
            FailureCategory.FILESYSTEM,
            "The validation report destination is unsafe.",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(serialize_report(report) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise CleanInstallError(
            FailureCategory.FILESYSTEM,
            "The validation report could not be written safely.",
        ) from error


def cleanup_validation_root(root: Path) -> None:
    """Remove only a validator-owned, non-linked child of the system temp directory."""
    resolved = root.resolve()
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    if (
        resolved.parent != temporary_parent
        or not resolved.name.startswith("Mira Clean Install Ünicode ")
        or root.is_symlink()
    ):
        raise CleanInstallError(
            FailureCategory.CLEANUP,
            "The clean-install validation root is not an owned temporary directory.",
        )
    deadline = time.monotonic() + 10
    while True:
        try:
            shutil.rmtree(resolved)
            return
        except OSError as error:
            if time.monotonic() >= deadline:
                raise CleanInstallError(
                    FailureCategory.CLEANUP,
                    "The owned clean-install validation root could not be removed.",
                ) from error
            time.sleep(0.2)


def validate_clean_install(
    bundle_path: Path,
    archive_path: Path,
    *,
    report_path: Path | None,
    preserve_diagnostics: bool,
    launch_timeout_seconds: int,
    shutdown_timeout_seconds: int,
) -> ValidationReport:
    """Execute every clean-profile, relocation, read-only, and archive scenario."""
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise CleanInstallError(
            FailureCategory.TOOLCHAIN,
            "Clean-install validation requires 64-bit Windows Python.",
        )
    if not 5 <= launch_timeout_seconds <= 180 or not 5 <= shutdown_timeout_seconds <= 60:
        raise CleanInstallError(
            FailureCategory.TOOLCHAIN,
            "Clean-install timeouts are outside the finite supported range.",
        )
    repository_root = Path(__file__).resolve().parents[1]
    try:
        source_layout = validate_static_bundle(bundle_path, repository_root)
    except VerificationError as error:
        raise CleanInstallError(
            FailureCategory.BUNDLE_CONTENT,
            "The source bundle failed static validation.",
        ) from error
    inspect_external_dependency_markers(source_layout.root, repository_root)
    source_manifest = fingerprint_tree(source_layout.root)
    metrics = bundle_metrics(source_layout)

    validation_root = Path(tempfile.mkdtemp(prefix="Mira Clean Install Ünicode ")).resolve()
    succeeded = False
    archive_created = False
    permissions_restored = True
    try:
        if validation_root.is_relative_to(repository_root.resolve()):
            raise CleanInstallError(
                FailureCategory.FILESYSTEM,
                "The clean-install staging root is inside the repository.",
            )
        if " " not in validation_root.name or not any(
            ord(char) > 127 for char in validation_root.name
        ):
            raise CleanInstallError(
                FailureCategory.FILESYSTEM,
                "The clean-install staging root lacks spaces or Unicode.",
            )
        staged_root = validation_root / "Installation Staging Ü" / "Depth One" / "MiraPortfolio"
        staged_manifest = copy_bundle(source_layout.root, staged_root)
        if staged_manifest != source_manifest:
            raise CleanInstallError(
                FailureCategory.BUNDLE_MUTATION,
                "The staged installation differs from the source bundle.",
            )
        staged_layout = validate_static_bundle(staged_root, repository_root)
        inspect_external_dependency_markers(staged_layout.root, repository_root)
        expected_head = bundled_single_head(staged_layout)

        observed: dict[str, tuple[str, ...]] = {}
        first_profile = create_clean_profile(validation_root, "First and Second Launch")
        observed["first_launch"] = launch_clean_application(
            staged_layout,
            first_profile,
            launch_timeout_seconds=launch_timeout_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
        )
        table_count = verify_clean_profile(
            first_profile,
            expected_head,
            repository_root,
            expected_start_count=1,
        )
        require_unchanged(staged_layout.root, staged_manifest)

        observed["second_launch"] = launch_clean_application(
            staged_layout,
            first_profile,
            launch_timeout_seconds=launch_timeout_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
        )
        verify_clean_profile(
            first_profile,
            expected_head,
            repository_root,
            expected_start_count=2,
        )
        require_unchanged(staged_layout.root, staged_manifest)

        relocated_root = (
            validation_root
            / "Relocated Installation With Spaces"
            / "Different"
            / "Directory"
            / "Depth Ü"
            / "MiraPortfolio"
        )
        relocated_manifest = copy_bundle(staged_layout.root, relocated_root)
        relocated_layout = validate_static_bundle(relocated_root, repository_root)
        relocated_profile = create_clean_profile(validation_root, "Relocated Launch Ü")
        observed["relocated_launch"] = launch_clean_application(
            relocated_layout,
            relocated_profile,
            launch_timeout_seconds=launch_timeout_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
        )
        verify_clean_profile(
            relocated_profile,
            expected_head,
            repository_root,
            expected_start_count=1,
        )
        require_unchanged(relocated_layout.root, relocated_manifest)

        read_only_root = validation_root / "Read Only Installation" / "MiraPortfolio"
        read_only_manifest = copy_bundle(staged_layout.root, read_only_root)
        read_only_layout = validate_static_bundle(read_only_root, repository_root)
        read_only_profile = create_clean_profile(validation_root, "Read Only Launch Ü")
        guard = ReadOnlyInstallation(read_only_layout.root)
        permissions_restored = False
        try:
            with guard:
                observed["read_only_install"] = launch_clean_application(
                    read_only_layout,
                    read_only_profile,
                    launch_timeout_seconds=launch_timeout_seconds,
                    shutdown_timeout_seconds=shutdown_timeout_seconds,
                )
                verify_clean_profile(
                    read_only_profile,
                    expected_head,
                    repository_root,
                    expected_start_count=1,
                )
                require_unchanged(read_only_layout.root, read_only_manifest)
        finally:
            permissions_restored = guard.restored
        if not permissions_restored:
            raise CleanInstallError(
                FailureCategory.READ_ONLY,
                "The temporary installation ACL was not restored.",
            )
        require_unchanged(read_only_layout.root, read_only_manifest)

        archive = create_deterministic_archive(source_layout.root, archive_path)
        archive_created = True
        extraction_root = validation_root / "Extracted Internal Alpha Candidate Ü"
        extracted_root = extract_and_verify_archive(
            archive_path,
            extraction_root,
            source_manifest,
        )
        extracted_layout = validate_static_bundle(extracted_root, repository_root)
        extracted_profile = create_clean_profile(validation_root, "Archive Extraction Launch Ü")
        observed["archive_extraction"] = launch_clean_application(
            extracted_layout,
            extracted_profile,
            launch_timeout_seconds=launch_timeout_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
        )
        verify_clean_profile(
            extracted_profile,
            expected_head,
            repository_root,
            expected_start_count=1,
        )
        require_unchanged(extracted_layout.root, source_manifest)
        require_unchanged(source_layout.root, source_manifest)

        profiles = (first_profile, relocated_profile, read_only_profile, extracted_profile)
        child_environment = clean_child_environment(os.environ, first_profile)
        environment_metrics = EnvironmentMetrics(
            windows_architecture=platform.machine(),
            python_available_to_child=shutil.which("python.exe", path=child_environment["PATH"])
            is not None,
            git_available_to_child=shutil.which("git.exe", path=child_environment["PATH"])
            is not None,
            repository_available_to_child=False,
            virtual_environment_available_to_child="VIRTUAL_ENV" in child_environment,
            administrator_rights_used=False,
            maximum_profile_path_length=max(len(str(profile.database)) for profile in profiles),
        )
        report = ValidationReport(
            result="passed",
            validation_timestamp_utc=_utc_timestamp(),
            failure_category=None,
            bundle=metrics,
            archive=archive,
            environment=environment_metrics,
            scenarios={name: "passed" for name in SCENARIO_NAMES},
            observed_processes={name: observed[name] for name in SCENARIO_NAMES},
            read_only_technique=READ_ONLY_TECHNIQUE,
            database_revision=expected_head,
            database_table_count=table_count,
        )
        destination = report_path or validation_root / "clean-install-validation-report.json"
        write_report(report, destination)
        print(serialize_report(report))
        succeeded = True
        return report
    finally:
        if not succeeded and archive_created:
            archive_path.unlink(missing_ok=True)
        if succeeded or (not preserve_diagnostics and permissions_restored):
            try:
                cleanup_validation_root(validation_root)
            except CleanInstallError:
                if succeeded:
                    raise
                print(
                    "Cleanup was incomplete; the owned diagnostic root was preserved.",
                    file=sys.stderr,
                )
        elif validation_root.exists():
            print("The owned diagnostic root was explicitly preserved.", file=sys.stderr)


def failed_report(category: FailureCategory) -> ValidationReport:
    """Create a bounded failure report without paths or raw exception details."""
    empty_bundle = BundleMetrics(0, 0, "0" * 64, "0" * 64)
    empty_archive = ArchiveMetrics(EXPECTED_ARCHIVE_NAME, 0, "0" * 64)
    environment = EnvironmentMetrics(
        windows_architecture=platform.machine(),
        python_available_to_child=False,
        git_available_to_child=False,
        repository_available_to_child=False,
        virtual_environment_available_to_child=False,
        administrator_rights_used=False,
        maximum_profile_path_length=0,
    )
    return ValidationReport(
        result="failed",
        validation_timestamp_utc=_utc_timestamp(),
        failure_category=category.value,
        bundle=empty_bundle,
        archive=empty_archive,
        environment=environment,
        scenarios={name: "not_run" for name in SCENARIO_NAMES},
        observed_processes={name: () for name in SCENARIO_NAMES},
        read_only_technique=READ_ONLY_TECHNIQUE,
        database_revision="unavailable",
        database_table_count=0,
    )


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--archive-path", type=Path, required=True)
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--preserve-diagnostics", action="store_true")
    parser.add_argument("--launch-timeout", type=int, default=60)
    parser.add_argument("--shutdown-timeout", type=int, default=20)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run validation and emit only sanitized failure details."""
    options = _parse_arguments(sys.argv[1:] if arguments is None else arguments)
    try:
        validate_clean_install(
            options.bundle,
            options.archive_path,
            report_path=options.report_path,
            preserve_diagnostics=options.preserve_diagnostics,
            launch_timeout_seconds=options.launch_timeout,
            shutdown_timeout_seconds=options.shutdown_timeout,
        )
    except CleanInstallError as error:
        report = failed_report(error.category)
        if options.report_path is not None:
            try:
                write_report(report, options.report_path)
            except CleanInstallError:
                pass
        print(serialize_report(report))
        print(f"Clean-install validation failed: {error}", file=sys.stderr)
        return 1
    except Exception:
        report = failed_report(FailureCategory.TOOLCHAIN)
        if options.report_path is not None:
            try:
                write_report(report, options.report_path)
            except CleanInstallError:
                pass
        print(serialize_report(report))
        print("Clean-install validation failed unexpectedly.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
