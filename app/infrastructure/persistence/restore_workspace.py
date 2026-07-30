"""Confined restore workspace paths, ownership checks, and artifact cleanup."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final
from uuid import UUID

from app.core.exceptions import (
    PendingRestoreCorruptError,
    RestoreAlreadyPendingError,
    RestoreApplicationError,
    RestoreError,
    RestoreNotSupportedError,
    RestoreRecoveryError,
    RestoreStagingError,
    RestoreVerificationError,
)
from app.core.settings import Settings
from app.infrastructure.persistence.sqlite_validation import (
    sqlite_file_path,
    sqlite_sidecar_paths,
)

RESTORE_DIRECTORY_NAME: Final = "restore"
PENDING_FILENAME: Final = "pending.json"
OPERATION_FILENAME: Final = "operation.json"
MAX_FILENAME_LENGTH: Final = 255
_SUSPICIOUS_PREFIXES: Final = (
    "rollback-",
    "staged-",
    ".restore-stage-",
    ".cancel-",
    ".pending.json.",
    ".operation.json.",
)


class RestoreDatabaseArtifact(StrEnum):
    """Kinds of request-owned SQLite files held in the restore workspace."""

    STAGED = "staged"
    ROLLBACK = "rollback"
    STAGING_TEMPORARY = "restore-stage"


@dataclass(frozen=True, slots=True)
class RestorePaths:
    """Resolved target and its same-filesystem restore workspace."""

    target: Path
    workspace: Path
    pending: Path
    operation: Path


@dataclass(frozen=True, slots=True)
class OwnedDatabase:
    """One database artifact proven to belong to a specific restore request."""

    path: Path
    request_id: UUID
    kind: RestoreDatabaseArtifact


def supported_restore_paths(settings: Settings) -> RestorePaths:
    """Resolve the configured file-based SQLite target and restore workspace."""
    try:
        target = sqlite_file_path(settings.database_url)
    except ValueError as error:
        raise RestoreNotSupportedError(
            "Restore requires a configured file-based SQLite database."
        ) from error
    workspace = target.parent / RESTORE_DIRECTORY_NAME
    return RestorePaths(
        target=target,
        workspace=workspace,
        pending=workspace / PENDING_FILENAME,
        operation=workspace / OPERATION_FILENAME,
    )


def optional_startup_restore_paths(settings: Settings) -> RestorePaths | None:
    """Return supported startup paths without weakening unsupported URL startup."""
    try:
        return supported_restore_paths(settings)
    except RestoreNotSupportedError:
        return None


def ensure_restore_workspace(workspace: Path) -> None:
    """Create one private, non-link restore workspace beside the target."""
    try:
        workspace.mkdir(mode=0o700, parents=False, exist_ok=True)
        require_restore_workspace(workspace)
        set_private_permissions(workspace, directory=True)
    except RestoreError:
        raise
    except OSError as error:
        raise RestoreStagingError("The restore staging directory could not be created.") from error


def require_restore_workspace(workspace: Path) -> None:
    """Reject symlinked, reparse-point, and non-directory workspaces."""
    if is_link_like(workspace) or not workspace.is_dir():
        raise PendingRestoreCorruptError("The restore staging location is not a regular directory.")


def require_no_pending_or_operation(paths: RestorePaths) -> None:
    """Require an idle workspace before accepting a new restore request."""
    if path_present(paths.pending):
        raise RestoreAlreadyPendingError(
            "A restore is already pending; cancel it before staging another."
        )
    if path_present(paths.operation):
        raise RestoreRecoveryError(
            "An interrupted restore must be recovered before staging another."
        )
    require_no_orphan_artifacts(paths)


def require_no_orphan_artifacts(paths: RestorePaths) -> None:
    """Fail closed when unreferenced transaction-shaped evidence exists."""
    suspicious = _suspicious_workspace_names(paths.workspace, permitted=frozenset())
    if suspicious:
        raise PendingRestoreCorruptError(
            "Unreferenced restore transaction files require manual recovery."
        )


def require_clean_pending_workspace(
    paths: RestorePaths,
    *,
    request_id: UUID,
    staged_database_filename: str,
) -> None:
    """Allow only artifacts explicitly owned by one pending request."""
    expected = staged_database_path(paths, request_id, staged_database_filename)
    permitted = frozenset({PENDING_FILENAME, expected.path.name})
    suspicious = _suspicious_workspace_names(paths.workspace, permitted=permitted)
    if suspicious:
        raise PendingRestoreCorruptError(
            "Unreferenced restore transaction files require manual recovery."
        )


def require_clean_operation_workspace(
    paths: RestorePaths,
    *,
    request_id: UUID,
    staged_database_filename: str,
    rollback_database_filename: str,
) -> None:
    """Allow only exact request-owned artifacts while an operation is durable."""
    staged = staged_database_path(paths, request_id, staged_database_filename)
    rollback = rollback_database_path(paths, request_id, rollback_database_filename)
    owned_databases = (staged.path, rollback.path)
    permitted = {
        PENDING_FILENAME,
        OPERATION_FILENAME,
        *(path.name for path in owned_databases),
        *(
            sidecar.name
            for database in owned_databases
            for sidecar in sqlite_sidecar_paths(database)
        ),
    }
    suspicious = _suspicious_workspace_names(
        paths.workspace,
        permitted=frozenset(permitted),
    )
    if suspicious:
        raise PendingRestoreCorruptError(
            "Unreferenced restore transaction files require manual recovery."
        )


def require_target_shape(target: Path) -> None:
    """Require an offline-replaceable target and ordinary writable sidecars."""
    if is_link_like(target) or (path_present(target) and not target.is_file()):
        raise RestoreApplicationError("The configured database target is not a regular file.")
    if target.exists() and not os.access(target, os.W_OK):
        raise RestoreApplicationError(
            "The configured database target is not writable for offline restore."
        )
    sidecars = sqlite_sidecar_paths(target)
    if not target.exists() and any(path_present(path) for path in sidecars):
        raise RestoreApplicationError("Orphaned database sidecars prevent a safe restore.")
    for sidecar in sidecars:
        if is_link_like(sidecar) or (path_present(sidecar) and not sidecar.is_file()):
            raise RestoreApplicationError("A database sidecar is not a regular file.")
        if sidecar.exists() and not os.access(sidecar, os.W_OK):
            raise RestoreApplicationError("A database sidecar is not writable for offline restore.")


def reserve_staging_database(paths: RestorePaths, request_id: UUID) -> OwnedDatabase:
    """Reserve a unique request-owned staging database in the restore workspace."""
    try:
        with NamedTemporaryFile(
            mode="wb",
            prefix=f".restore-stage-{request_id}-",
            suffix=".sqlite",
            dir=paths.workspace,
            delete=False,
        ) as temporary:
            path = Path(temporary.name)
        artifact = temporary_database_path(paths, request_id, path)
        set_private_permissions(artifact.path)
        return artifact
    except RestoreError:
        raise
    except OSError as error:
        raise RestoreStagingError(
            "A temporary restore staging file could not be created."
        ) from error


def staged_database_path(
    paths: RestorePaths,
    request_id: UUID,
    filename: str,
) -> OwnedDatabase:
    """Validate the exact durable staged filename for one request."""
    expected = f"staged-{request_id}.sqlite"
    if filename != expected:
        raise PendingRestoreCorruptError("Pending restore staged filename is invalid.")
    return _owned_database(paths, request_id, filename, RestoreDatabaseArtifact.STAGED)


def rollback_database_path(
    paths: RestorePaths,
    request_id: UUID,
    filename: str,
) -> OwnedDatabase:
    """Validate the exact rollback filename for one request."""
    expected = f"rollback-{request_id}.sqlite"
    if filename != expected:
        raise RestoreRecoveryError("Restore operation rollback filename is invalid.")
    return _owned_database(paths, request_id, filename, RestoreDatabaseArtifact.ROLLBACK)


def temporary_database_path(
    paths: RestorePaths,
    request_id: UUID,
    path: Path,
) -> OwnedDatabase:
    """Validate a unique temporary staging filename and workspace confinement."""
    prefix = f".restore-stage-{request_id}-"
    if (
        not path.name.startswith(prefix)
        or not path.name.endswith(".sqlite")
        or len(path.name) <= len(prefix) + len(".sqlite")
    ):
        raise RestoreStagingError("Temporary restore staging filename is invalid.")
    require_confined_path(paths.workspace, path)
    return OwnedDatabase(path, request_id, RestoreDatabaseArtifact.STAGING_TEMPORARY)


def cancellation_metadata_path(paths: RestorePaths, request_id: UUID) -> Path:
    """Return the exact request-owned cancellation holding filename."""
    return confined_filename(paths.workspace, f".cancel-{request_id}.json")


def require_confined_path(directory: Path, candidate: Path) -> None:
    """Require a direct child whose normalized parent remains the workspace."""
    if candidate.parent != directory:
        raise PendingRestoreCorruptError(
            "Restore metadata references a file outside its workspace."
        )
    try:
        if candidate.parent.resolve(strict=True) != directory.resolve(strict=True):
            raise PendingRestoreCorruptError(
                "Restore metadata references a file outside its workspace."
            )
    except OSError as error:
        raise PendingRestoreCorruptError(
            "The restore staging location could not be resolved safely."
        ) from error


def confined_filename(directory: Path, filename: str) -> Path:
    """Return one validated basename directly beneath a trusted workspace."""
    require_filename(filename, "restore")
    candidate = directory / filename
    require_confined_path(directory, candidate)
    return candidate


def require_filename(value: str, label: str) -> None:
    """Reject separators, traversal, empty names, and oversized basenames."""
    if (
        not value
        or len(value) > MAX_FILENAME_LENGTH
        or Path(value).name != value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
        or ".." in Path(value).parts
    ):
        raise RestoreVerificationError(f"Restore {label} filename is invalid.")


def require_regular_owned_database(
    artifact: OwnedDatabase,
    error: RestoreError,
) -> Path:
    """Require an owned database to be an ordinary non-link regular file."""
    require_confined_path(artifact.path.parent, artifact.path)
    if is_link_like(artifact.path) or not artifact.path.is_file():
        raise error
    return artifact.path


def flush_owned_database(
    artifact: OwnedDatabase,
    error_type: type[RestoreError],
) -> None:
    """Synchronize a closed request-owned SQLite file before replacement."""
    require_regular_owned_database(
        artifact,
        error_type("The restore database could not be synchronized safely."),
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(artifact.path, os.O_RDWR)
        os.fsync(descriptor)
    except OSError as error:
        raise error_type("The restore database could not be synchronized safely.") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def remove_owned_database(
    artifact: OwnedDatabase | None,
    error_type: type[RestoreError],
) -> None:
    """Remove only an explicitly validated request-owned database and sidecars."""
    if artifact is None:
        return
    for path in (artifact.path, *sqlite_sidecar_paths(artifact.path)):
        require_confined_path(artifact.path.parent, path)
        if is_link_like(path):
            raise error_type("A restore transaction artifact is a link or reparse point.")
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise error_type("Restore transaction files could not be removed safely.") from error
    sync_directory_best_effort(artifact.path.parent)


def remove_owned_sidecars(
    artifact: OwnedDatabase,
    error_type: type[RestoreError],
) -> None:
    """Remove only known sidecars of one validated request-owned database."""
    for path in sqlite_sidecar_paths(artifact.path):
        require_confined_path(artifact.path.parent, path)
        if is_link_like(path):
            raise error_type("A restore transaction sidecar is a link or reparse point.")
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise error_type("Restore database sidecars could not be removed safely.") from error
    sync_directory_best_effort(artifact.path.parent)


def best_effort_remove_owned_database(artifact: OwnedDatabase | None) -> None:
    """Best-effort cleanup that never follows or deletes link-like evidence."""
    if artifact is None:
        return
    for path in (artifact.path, *sqlite_sidecar_paths(artifact.path)):
        try:
            require_confined_path(artifact.path.parent, path)
            if is_link_like(path):
                continue
            path.unlink(missing_ok=True)
        except (OSError, RestoreError):
            pass


def remove_target_sidecars(
    target: Path,
    error_type: type[RestoreError],
) -> None:
    """Remove only known sidecars of the configured target after validation."""
    for path in sqlite_sidecar_paths(target):
        if is_link_like(path):
            raise error_type("A restore database sidecar is a link or reparse point.")
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise error_type("Restore database sidecars could not be removed safely.") from error
    sync_directory_best_effort(target.parent)


def remove_exact_metadata(
    path: Path,
    *,
    expected_name: str,
    error_type: type[RestoreError],
    missing_ok: bool,
) -> None:
    """Remove one exact trusted metadata filename without following links."""
    if path.name != expected_name or path.parent.name != RESTORE_DIRECTORY_NAME:
        raise error_type("Restore metadata cleanup path is not trusted.")
    if is_link_like(path):
        raise error_type("Restore metadata is a link or reparse point.")
    try:
        path.unlink(missing_ok=missing_ok)
    except OSError as error:
        raise error_type("Restore metadata could not be removed safely.") from error
    sync_directory_best_effort(path.parent)


def set_private_permissions(path: Path, *, directory: bool = False) -> None:
    """Apply owner-only POSIX permissions; retain the Windows ACL fallback."""
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        if os.name != "nt":
            raise


def sync_directory_best_effort(directory: Path) -> None:
    """Best-effort parent durability; Windows has no portable directory fsync."""
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def remove_empty_workspace(workspace: Path) -> None:
    """Remove only the validated workspace itself when it is empty."""
    try:
        require_restore_workspace(workspace)
        workspace.rmdir()
    except (OSError, RestoreError):
        pass


def path_present(path: Path) -> bool:
    """Detect ordinary paths, broken symlinks, and supported reparse points."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def is_link_like(path: Path) -> bool:
    """Detect symlinks and Windows reparse points without following them."""
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _owned_database(
    paths: RestorePaths,
    request_id: UUID,
    filename: str,
    kind: RestoreDatabaseArtifact,
) -> OwnedDatabase:
    path = confined_filename(paths.workspace, filename)
    return OwnedDatabase(path=path, request_id=request_id, kind=kind)


def _suspicious_workspace_names(
    workspace: Path,
    *,
    permitted: frozenset[str],
) -> tuple[str, ...]:
    try:
        return tuple(
            path.name
            for path in workspace.iterdir()
            if path.name not in permitted and path.name.startswith(_SUSPICIOUS_PREFIXES)
        )
    except OSError as error:
        raise PendingRestoreCorruptError(
            "The restore staging location could not be inspected."
        ) from error


__all__ = [
    "OPERATION_FILENAME",
    "PENDING_FILENAME",
    "RESTORE_DIRECTORY_NAME",
    "OwnedDatabase",
    "RestoreDatabaseArtifact",
    "RestorePaths",
    "best_effort_remove_owned_database",
    "cancellation_metadata_path",
    "confined_filename",
    "ensure_restore_workspace",
    "flush_owned_database",
    "is_link_like",
    "optional_startup_restore_paths",
    "path_present",
    "remove_empty_workspace",
    "remove_exact_metadata",
    "remove_owned_database",
    "remove_owned_sidecars",
    "remove_target_sidecars",
    "require_clean_pending_workspace",
    "require_clean_operation_workspace",
    "require_confined_path",
    "require_filename",
    "require_no_orphan_artifacts",
    "require_no_pending_or_operation",
    "require_regular_owned_database",
    "require_restore_workspace",
    "require_target_shape",
    "reserve_staging_database",
    "rollback_database_path",
    "set_private_permissions",
    "staged_database_path",
    "supported_restore_paths",
    "sync_directory_best_effort",
    "temporary_database_path",
]
