"""Verify the built Windows one-folder bundle through isolated real GUI launches."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import closing
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from alembic.config import Config
from alembic.script import ScriptDirectory

sys.dont_write_bytecode = True

APP_NAME: Final = "Mira Portfolio"
EXECUTABLE_NAME: Final = "MiraPortfolio.exe"
INTERNAL_DIRECTORY_NAME: Final = "_internal"
LOG_FILENAME: Final = "mira-portfolio.log"
PREFERENCES_FILENAME: Final = "preferences.json"
BACKUP_EXTENSION: Final = ".mirabackup"
SUPPORT_EXTENSION: Final = ".mirasupport"
RESTORE_DIRECTORY_NAME: Final = "restore"
WINDOWS_GUI_SUBSYSTEM: Final = 2
WM_CLOSE: Final = 0x0010
TH32CS_SNAPPROCESS: Final = 0x00000002
EXPECTED_TABLES: Final = frozenset(
    {
        "alembic_version",
        "assets",
        "portfolios",
        "portfolio_assets",
        "transactions",
        "price_history",
        "snapshots",
    }
)
PASSTHROUGH_ENVIRONMENT: Final = frozenset(
    {
        "COMSPEC",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMROOT",
        "WINDIR",
    }
)


class VerificationError(RuntimeError):
    """Raised for one privacy-safe bundle acceptance failure."""


@dataclass(frozen=True, slots=True)
class BundleLayout:
    """Validated immutable paths inside one bundle folder."""

    root: Path
    executable: Path
    internal: Path
    alembic_ini: Path
    migrations: Path
    qwindows_plugin: Path


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    """All writable locations owned by one isolated launch hierarchy."""

    root: Path
    working_directory: Path
    data_directory: Path
    cache_directory: Path
    database_directory: Path
    export_directory: Path
    backup_directory: Path
    log_directory: Path
    database: Path
    appdata_directory: Path
    local_appdata_directory: Path
    profile_directory: Path
    temporary_directory: Path


@dataclass(frozen=True, slots=True)
class DatabaseVerification:
    """Non-sensitive database acceptance result."""

    revision: str
    tables: frozenset[str]


class _ProcessEntry(ctypes.Structure):
    """Windows Toolhelp entry for launcher descendant discovery."""

    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


@dataclass(frozen=True, slots=True)
class ProcessTreeObservation:
    """PID-scoped process identities without exposing executable paths."""

    process_ids: frozenset[int]
    executable_names: tuple[str, ...]


def expected_revision_files(repository_root: Path) -> tuple[str, ...]:
    """Return the tracked Alembic revision filenames expected in a bundle."""
    versions = repository_root / "migrations" / "versions"
    names = tuple(
        path.name
        for path in sorted(versions.glob("*.py"))
        if path.is_file() and path.name != "__init__.py"
    )
    if not names:
        raise VerificationError("No tracked Alembic revisions were found.")
    return names


def validate_static_bundle(bundle_path: Path, repository_root: Path) -> BundleLayout:
    """Validate immutable structure and forbidden-content rules without execution."""
    root = bundle_path.resolve()
    if not root.is_dir() or root.is_symlink():
        raise VerificationError("The bundle directory is missing or unsafe.")

    executable = root / EXECUTABLE_NAME
    internal = root / INTERNAL_DIRECTORY_NAME
    alembic_ini = internal / "alembic.ini"
    migrations = internal / "migrations"
    required = (
        executable,
        internal,
        alembic_ini,
        migrations / "env.py",
        migrations / "script.py.mako",
    )
    if not executable.is_file() or executable.is_symlink():
        raise VerificationError("MiraPortfolio.exe is missing or unsafe.")
    if not internal.is_dir() or any(not path.exists() for path in required[2:]):
        raise VerificationError("The bundled Alembic resources are incomplete.")
    for revision in expected_revision_files(repository_root):
        if not (migrations / "versions" / revision).is_file():
            raise VerificationError("A tracked Alembic revision is missing from the bundle.")

    plugins = tuple(
        path
        for path in internal.rglob("qwindows.dll")
        if path.is_file() and path.parent.name.casefold() == "platforms"
    )
    if len(plugins) != 1:
        raise VerificationError("The required Qt Windows platform plugin is missing or ambiguous.")

    _require_gui_subsystem(executable)
    _reject_forbidden_bundle_content(root)
    return BundleLayout(root, executable, internal, alembic_ini, migrations, plugins[0])


def _require_gui_subsystem(executable: Path) -> None:
    try:
        with executable.open("rb") as stream:
            header = stream.read(4096)
        if len(header) < 64 or header[:2] != b"MZ":
            raise VerificationError("The public executable is not a valid Windows PE file.")
        pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
        subsystem_offset = pe_offset + 4 + 20 + 68
        if len(header) < subsystem_offset + 2 or header[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise VerificationError("The public executable has an invalid PE header.")
        subsystem = struct.unpack_from("<H", header, subsystem_offset)[0]
    except OSError as error:
        raise VerificationError("The public executable could not be inspected.") from error
    if subsystem != WINDOWS_GUI_SUBSYSTEM:
        raise VerificationError("The public executable does not use the Windows GUI subsystem.")


def _reject_forbidden_bundle_content(root: Path) -> None:
    for path in root.rglob("*"):
        relative_parts = tuple(part.casefold() for part in path.relative_to(root).parts)
        name = path.name.casefold()
        suffix = path.suffix.casefold()
        if ".git" in relative_parts or "tests" in relative_parts:
            raise VerificationError("Repository metadata or tests were embedded in the bundle.")
        if name == ".env":
            raise VerificationError("An environment file was embedded in the bundle.")
        if name == PREFERENCES_FILENAME:
            raise VerificationError("A preference file was embedded in the bundle.")
        if suffix in {".db", ".sqlite", ".sqlite3"} or name.endswith(("-wal", "-shm", "-journal")):
            raise VerificationError("A database artifact was embedded in the bundle.")
        if suffix in {BACKUP_EXTENSION, SUPPORT_EXTENSION}:
            raise VerificationError("A runtime archive was embedded in the bundle.")
        if RESTORE_DIRECTORY_NAME in relative_parts:
            raise VerificationError("A restore workspace was embedded in the bundle.")


def fingerprint_tree(root: Path) -> dict[str, tuple[int, str]]:
    """Hash every regular bundle file using stable relative names."""
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise VerificationError("The bundle contains an unexpected symbolic link.")
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            size = path.stat().st_size
        except OSError as error:
            raise VerificationError("A bundle file could not be fingerprinted.") from error
        result[path.relative_to(root).as_posix()] = (size, digest.hexdigest())
    return result


def create_runtime_layout(root: Path) -> RuntimeLayout:
    """Create only isolation scaffolding; the application creates its own runtime paths."""
    layout = RuntimeLayout(
        root=root,
        working_directory=root / "Working Directory",
        data_directory=root / "Mira Data",
        cache_directory=root / "Mira Cache",
        database_directory=root / "Mira Data" / "database",
        export_directory=root / "Mira Data" / "exports",
        backup_directory=root / "Mira Data" / "backups",
        log_directory=root / "Mira Logs",
        database=root / "Mira Data" / "database" / "portfolio.db",
        appdata_directory=root / "Windows AppData" / "Roaming",
        local_appdata_directory=root / "Windows AppData" / "Local",
        profile_directory=root / "Windows Profile",
        temporary_directory=root / "Windows Temp",
    )
    for directory in (
        layout.root,
        layout.working_directory,
        layout.appdata_directory,
        layout.local_appdata_directory,
        layout.profile_directory,
        layout.temporary_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return layout


def isolated_child_environment(
    source: Mapping[str, str],
    layout: RuntimeLayout,
) -> dict[str, str]:
    """Build a minimal Windows environment with no inherited Mira or Qt settings."""
    normalized = {key.upper(): value for key, value in source.items()}
    environment = {key: normalized[key] for key in PASSTHROUGH_ENVIRONMENT if key in normalized}
    if "SYSTEMROOT" not in environment:
        raise VerificationError("The Windows SystemRoot environment is unavailable.")

    environment.update(
        {
            "APPDATA": str(layout.appdata_directory),
            "LOCALAPPDATA": str(layout.local_appdata_directory),
            "USERPROFILE": str(layout.profile_directory),
            "TEMP": str(layout.temporary_directory),
            "TMP": str(layout.temporary_directory),
            "MIRA_DATA_DIRECTORY": str(layout.data_directory),
            "MIRA_CACHE_DIRECTORY": str(layout.cache_directory),
            "MIRA_DATABASE_DIRECTORY": str(layout.database_directory),
            "MIRA_EXPORT_DIRECTORY": str(layout.export_directory),
            "MIRA_BACKUP_DIRECTORY": str(layout.backup_directory),
            "MIRA_LOG_DIRECTORY": str(layout.log_directory),
            "MIRA_DATABASE_PATH": str(layout.database),
            "MIRA_DATABASE_URL": f"sqlite:///{layout.database.as_posix()}",
        }
    )
    return environment


def observe_process_tree(root_process_id: int) -> ProcessTreeObservation:
    """Return one Toolhelp snapshot of a process and every current descendant."""
    if os.name != "nt":
        raise VerificationError("Process discovery requires Windows.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise VerificationError("The launched process tree could not be inspected.")
    relationships: list[tuple[int, int, str]] = []
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(_ProcessEntry)
    try:
        available = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while available:
            relationships.append(
                (
                    int(entry.th32ProcessID),
                    int(entry.th32ParentProcessID),
                    Path(entry.szExeFile).name,
                )
            )
            available = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)

    process_ids = {root_process_id}
    changed = True
    while changed:
        changed = False
        for process_id, parent_process_id, _ in relationships:
            if parent_process_id in process_ids and process_id not in process_ids:
                process_ids.add(process_id)
                changed = True
    names = tuple(
        sorted(
            {
                executable_name
                for process_id, _, executable_name in relationships
                if process_id in process_ids
            },
            key=str.casefold,
        )
    )
    return ProcessTreeObservation(frozenset(process_ids), names)


def _process_tree_ids(root_process_id: int) -> frozenset[int]:
    return observe_process_tree(root_process_id).process_ids


def _visible_windows_for_processes(
    process_ids: frozenset[int],
) -> tuple[tuple[int, str], ...]:
    if os.name != "nt":
        raise VerificationError("Window discovery requires Windows.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    windows: list[tuple[int, str]] = []

    def collect(window: int, _: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value not in process_ids or not user32.IsWindowVisible(window):
            return True
        length = int(user32.GetWindowTextLengthW(window))
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window, buffer, len(buffer))
        windows.append((int(window), buffer.value))
        return True

    callback = callback_type(collect)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    if not user32.EnumWindows(callback, 0):
        raise VerificationError("Top-level Windows could not be enumerated.")
    return tuple(windows)


def _wait_for_main_window(
    process: subprocess.Popen[bytes],
    timeout_seconds: int,
) -> tuple[int, str]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise VerificationError("The bundled application exited before showing its window.")
        windows = _visible_windows_for_processes(_process_tree_ids(process.pid))
        for window, title in windows:
            if title == APP_NAME:
                return window, title
        if any("unexpected error" in title.casefold() for _, title in windows):
            raise VerificationError("A fatal-error dialog was the only visible application window.")
        time.sleep(0.1)
    raise VerificationError("The bundled application did not show its main window in time.")


def _post_close(window: int) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    if not user32.PostMessageW(window, WM_CLOSE, 0, 0):
        raise VerificationError("The normal Windows close message could not be posted.")


def _cleanup_failed_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        for window, _ in _visible_windows_for_processes(_process_tree_ids(process.pid)):
            _post_close(window)
        process.wait(timeout=5)
    except Exception:
        process.kill()
        process.wait(timeout=10)


def launch_and_close(
    layout: BundleLayout,
    runtime: RuntimeLayout,
    *,
    launch_timeout_seconds: int,
    shutdown_timeout_seconds: int,
) -> str:
    """Launch the real GUI, find its PID-owned MainWindow, and close it normally."""
    environment = isolated_child_environment(os.environ, runtime)
    process = subprocess.Popen(
        [str(layout.executable)],
        cwd=runtime.working_directory,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    try:
        window, title = _wait_for_main_window(process, launch_timeout_seconds)
        _post_close(window)
        try:
            exit_code = process.wait(timeout=shutdown_timeout_seconds)
        except subprocess.TimeoutExpired as error:
            raise VerificationError("The bundled application did not shut down in time.") from error
        if exit_code != 0:
            raise VerificationError("The bundled application did not exit successfully.")
        return title
    except Exception:
        _cleanup_failed_process(process)
        raise


def bundled_single_head(layout: BundleLayout) -> str:
    """Load the bundled migration graph and require its exact single head."""
    config = Config(str(layout.alembic_ini))
    config.set_main_option("script_location", str(layout.migrations))
    config.set_main_option("prepend_sys_path", str(layout.internal))
    try:
        heads = ScriptDirectory.from_config(config).get_heads()
    except Exception as error:
        raise VerificationError("The bundled Alembic graph could not be loaded.") from error
    if len(heads) != 1:
        raise VerificationError("The bundled Alembic graph does not have exactly one head.")
    return heads[0]


def verify_database(database: Path, expected_head: str) -> DatabaseVerification:
    """Read only the isolated runtime database and verify revision and schema."""
    if not database.is_file() or database.is_symlink():
        raise VerificationError("The isolated first-run database was not created safely.")
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if integrity != ("ok",):
                raise VerificationError("The isolated SQLite database failed quick_check.")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise VerificationError("The isolated SQLite database failed foreign-key checks.")
            tables = frozenset(
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                if isinstance(row[0], str)
            )
            revision_rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
            snapshot_count = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()
    except (OSError, sqlite3.Error) as error:
        raise VerificationError("The isolated SQLite database could not be validated.") from error
    if not EXPECTED_TABLES.issubset(tables):
        raise VerificationError("The isolated database schema is incomplete.")
    if revision_rows != [(expected_head,)]:
        raise VerificationError("The isolated database revision does not match the bundled head.")
    if snapshot_count != (0,):
        raise VerificationError("Startup created an unexpected automatic snapshot.")
    return DatabaseVerification(expected_head, tables)


def verify_runtime_artifacts(
    runtime: RuntimeLayout,
    expected_head: str,
    repository_root: Path,
    *,
    expected_start_count: int,
) -> DatabaseVerification:
    """Validate isolated data, logs, and absence of automatic runtime artifacts."""
    database = verify_database(runtime.database, expected_head)
    log_path = runtime.log_directory / LOG_FILENAME
    if not log_path.is_file() or log_path.is_symlink():
        raise VerificationError("The persistent startup log was not created safely.")
    try:
        log_text = log_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise VerificationError("The persistent startup log is not valid UTF-8.") from error
    if log_text.count("Mira Portfolio started") < expected_start_count:
        raise VerificationError("The persistent log does not prove MainWindow startup.")
    lowered_log = log_text.casefold()
    if "fatal_unhandled_error" in lowered_log or "traceback (most recent call last)" in lowered_log:
        raise VerificationError("The persistent log contains a fatal incident or traceback.")
    repository_text = str(repository_root.resolve()).casefold()
    if repository_text in lowered_log or repository_text.replace("\\", "/") in lowered_log:
        raise VerificationError("The persistent log contains the developer repository path.")
    if "sqlite:" in lowered_log:
        raise VerificationError("The persistent log contains a database URL.")

    preferences = runtime.data_directory / "settings" / PREFERENCES_FILENAME
    restore = runtime.database_directory / RESTORE_DIRECTORY_NAME
    support = runtime.data_directory / "support"
    if preferences.exists():
        raise VerificationError("Startup created an unexpected preference file.")
    if restore.exists():
        raise VerificationError("Startup created an unexpected restore workspace.")
    if support.exists() and any(support.rglob("*")):
        raise VerificationError("Startup created an unexpected support artifact.")
    if runtime.backup_directory.exists() and any(runtime.backup_directory.rglob("*")):
        raise VerificationError("Startup created an unexpected automatic backup.")
    if any(runtime.root.rglob(f"*{BACKUP_EXTENSION}")) or any(
        runtime.root.rglob(f"*{SUPPORT_EXTENSION}")
    ):
        raise VerificationError("Startup created an unexpected runtime archive.")
    return database


def _require_unchanged(
    root: Path,
    expected: dict[str, tuple[int, str]],
) -> None:
    if fingerprint_tree(root) != expected:
        raise VerificationError("Application execution modified the immutable bundle.")


def verify_windows_bundle(
    bundle_path: Path,
    *,
    keep_on_failure: bool,
    launch_timeout_seconds: int,
    shutdown_timeout_seconds: int,
) -> None:
    """Run static, first-launch, second-launch, and relocation acceptance checks."""
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise VerificationError("Bundle verification requires 64-bit Windows Python.")
    if not 5 <= launch_timeout_seconds <= 180 or not 5 <= shutdown_timeout_seconds <= 60:
        raise VerificationError("Bundle verification timeouts are outside the safe finite range.")

    repository_root = Path(__file__).resolve().parents[1]
    layout = validate_static_bundle(bundle_path, repository_root)
    original_fingerprint = fingerprint_tree(layout.root)
    expected_head = bundled_single_head(layout)

    verification_root = Path(tempfile.mkdtemp(prefix="Mira Bundle Verify Ünicode "))
    succeeded = False
    try:
        if " " not in verification_root.name or not any(
            ord(character) > 127 for character in verification_root.name
        ):
            raise VerificationError("The verifier could not create the required Unicode test root.")

        runtime = create_runtime_layout(verification_root / "Primary Runtime Ü")
        try:
            first_title = launch_and_close(
                layout,
                runtime,
                launch_timeout_seconds=launch_timeout_seconds,
                shutdown_timeout_seconds=shutdown_timeout_seconds,
            )
        except VerificationError as error:
            raise VerificationError(f"First launch failed: {error}") from error
        first_database = verify_runtime_artifacts(
            runtime,
            expected_head,
            repository_root,
            expected_start_count=1,
        )
        _require_unchanged(layout.root, original_fingerprint)

        try:
            second_title = launch_and_close(
                layout,
                runtime,
                launch_timeout_seconds=launch_timeout_seconds,
                shutdown_timeout_seconds=shutdown_timeout_seconds,
            )
        except VerificationError as error:
            raise VerificationError(f"Second launch failed: {error}") from error
        second_database = verify_runtime_artifacts(
            runtime,
            expected_head,
            repository_root,
            expected_start_count=2,
        )
        _require_unchanged(layout.root, original_fingerprint)

        relocated_root = verification_root / "Relocated Bundle With Spaces"
        shutil.copytree(layout.root, relocated_root)
        relocated_layout = validate_static_bundle(relocated_root, repository_root)
        relocated_fingerprint = fingerprint_tree(relocated_layout.root)
        if bundled_single_head(relocated_layout) != expected_head:
            raise VerificationError(
                "The relocated migration head differs from the original bundle."
            )
        relocated_runtime = create_runtime_layout(verification_root / "Relocated Runtime Ü")
        try:
            relocated_title = launch_and_close(
                relocated_layout,
                relocated_runtime,
                launch_timeout_seconds=launch_timeout_seconds,
                shutdown_timeout_seconds=shutdown_timeout_seconds,
            )
        except VerificationError as error:
            raise VerificationError(f"Relocated launch failed: {error}") from error
        relocated_database = verify_runtime_artifacts(
            relocated_runtime,
            expected_head,
            repository_root,
            expected_start_count=1,
        )
        _require_unchanged(relocated_layout.root, relocated_fingerprint)
        _require_unchanged(layout.root, original_fingerprint)

        print(f"First launch window: {first_title}")
        print(f"Second launch window: {second_title}")
        print(f"Relocated launch window: {relocated_title}")
        print(f"Alembic head: {expected_head}")
        print(f"Verified schema table count: {len(first_database.tables)}")
        print(
            "Database revisions: "
            f"{first_database.revision}, {second_database.revision}, "
            f"{relocated_database.revision}"
        )
        print("Bundle immutability: verified")
        print("Runtime isolation: verified")
        succeeded = True
    finally:
        if succeeded or not keep_on_failure:
            shutil.rmtree(verification_root, ignore_errors=True)
        elif verification_root.exists():
            print(f"Preserved diagnostic root: {verification_root}", file=sys.stderr)


def _parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="Path to dist/MiraPortfolio")
    parser.add_argument("--keep-on-failure", action="store_true")
    parser.add_argument("--launch-timeout", type=int, default=60)
    parser.add_argument("--shutdown-timeout", type=int, default=20)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run verification without exposing raw exception values."""
    options = _parse_arguments(sys.argv[1:] if arguments is None else arguments)
    try:
        verify_windows_bundle(
            options.bundle,
            keep_on_failure=options.keep_on_failure,
            launch_timeout_seconds=options.launch_timeout,
            shutdown_timeout_seconds=options.shutdown_timeout,
        )
    except VerificationError as error:
        print(f"Bundle verification failed: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("Bundle verification failed unexpectedly.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
