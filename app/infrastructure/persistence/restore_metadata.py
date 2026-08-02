"""Strict restore metadata models, parsing, serialization, and durable writes."""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final, Never
from uuid import UUID

from app.application.backup import BackupKind
from app.core.exceptions import (
    PendingRestoreCorruptError,
    RestoreAlreadyPendingError,
    RestoreApplicationError,
    RestoreError,
    RestoreRecoveryError,
    RestoreStagingError,
    RestoreVerificationError,
)
from app.infrastructure.persistence.database_backup import (
    BACKUP_EXTENSION,
    MAX_DATABASE_BYTES,
)
from app.infrastructure.persistence.restore_failpoints import (
    RestoreFailpoint,
    RestoreFailpointCallback,
    reach_failpoint,
)
from app.infrastructure.persistence.restore_state import RestoreOperationState
from app.infrastructure.persistence.restore_workspace import (
    OPERATION_FILENAME,
    PENDING_FILENAME,
    RestorePaths,
    is_link_like,
    path_present,
    require_filename,
    require_restore_workspace,
    set_private_permissions,
    sync_directory_best_effort,
)

PENDING_FORMAT_VERSION: Final = 1
OPERATION_FORMAT_VERSION: Final = 1
UNVERSIONED_REVISION: Final = "unversioned"
MAX_PENDING_BYTES: Final = 32 * 1024
MAX_OPERATION_BYTES: Final = 64 * 1024
MAX_TEXT_LENGTH: Final = 255
FILE_KEYS: Final = ("database", "wal", "shm", "journal")
PENDING_FIELDS: Final = frozenset(
    {
        "format_version",
        "request_id",
        "created_at_utc",
        "staged_database_filename",
        "source_backup_filename",
        "source_backup_sha256",
        "source_application_version",
        "source_alembic_revision",
        "source_database_size_bytes",
        "source_database_sha256",
        "backup_kind",
        "expected_database_size_bytes",
        "expected_database_sha256",
        "expected_alembic_revision",
    }
)
OPERATION_FIELDS: Final = frozenset(
    {
        "format_version",
        "request_id",
        "state",
        "created_at_utc",
        "updated_at_utc",
        "target_database_filename",
        "staged_database_filename",
        "rollback_database_filename",
        "pre_restore_backup_filename",
        "source_backup_filename",
        "source_application_version",
        "source_alembic_revision",
        "source_database_size_bytes",
        "source_database_sha256",
        "backup_kind",
        "expected_database_size_bytes",
        "expected_database_sha256",
        "expected_alembic_revision",
        "original_files",
    }
)
STORED_FILE_FIELDS: Final = frozenset({"filename", "size_bytes", "modified_ns", "sha256"})


@dataclass(frozen=True, slots=True)
class PendingRestore:
    """Durable request metadata published after isolated staging succeeds."""

    request_id: UUID
    created_at: datetime
    staged_database_filename: str
    source_backup_filename: str
    source_backup_sha256: str
    source_application_version: str
    source_alembic_revision: str
    source_database_size: int
    source_database_sha256: str
    backup_kind: BackupKind
    expected_database_size: int
    expected_database_sha256: str
    expected_alembic_revision: str


@dataclass(frozen=True, slots=True)
class StoredFile:
    """Byte and filesystem identity for an original database artifact."""

    filename: str
    size: int
    modified_ns: int
    sha256: str


type OriginalFiles = tuple[
    StoredFile | None,
    StoredFile | None,
    StoredFile | None,
    StoredFile | None,
]


@dataclass(frozen=True, slots=True)
class RestoreOperation:
    """One immutable snapshot of the durable restore transaction journal."""

    request_id: UUID
    state: RestoreOperationState
    created_at: datetime
    updated_at: datetime
    target_database_filename: str
    staged_database_filename: str
    rollback_database_filename: str
    pre_restore_backup_filename: str | None
    source_backup_filename: str
    source_application_version: str
    source_alembic_revision: str
    source_database_size: int
    source_database_sha256: str
    backup_kind: BackupKind
    expected_database_size: int
    expected_database_sha256: str
    expected_alembic_revision: str
    original_files: OriginalFiles


def pending_payload(pending: PendingRestore) -> dict[str, object]:
    """Serialize pending metadata without changing the accepted format."""
    return {
        "format_version": PENDING_FORMAT_VERSION,
        "request_id": str(pending.request_id),
        "created_at_utc": format_utc(pending.created_at),
        "staged_database_filename": pending.staged_database_filename,
        "source_backup_filename": pending.source_backup_filename,
        "source_backup_sha256": pending.source_backup_sha256,
        "source_application_version": pending.source_application_version,
        "source_alembic_revision": pending.source_alembic_revision,
        "source_database_size_bytes": pending.source_database_size,
        "source_database_sha256": pending.source_database_sha256,
        "backup_kind": pending.backup_kind.value,
        "expected_database_size_bytes": pending.expected_database_size,
        "expected_database_sha256": pending.expected_database_sha256,
        "expected_alembic_revision": pending.expected_alembic_revision,
    }


def operation_payload(operation: RestoreOperation) -> dict[str, object]:
    """Serialize the operation journal without changing the accepted format."""
    return {
        "format_version": OPERATION_FORMAT_VERSION,
        "request_id": str(operation.request_id),
        "state": operation.state.value,
        "created_at_utc": format_utc(operation.created_at),
        "updated_at_utc": format_utc(operation.updated_at),
        "target_database_filename": operation.target_database_filename,
        "staged_database_filename": operation.staged_database_filename,
        "rollback_database_filename": operation.rollback_database_filename,
        "pre_restore_backup_filename": operation.pre_restore_backup_filename,
        "source_backup_filename": operation.source_backup_filename,
        "source_application_version": operation.source_application_version,
        "source_alembic_revision": operation.source_alembic_revision,
        "source_database_size_bytes": operation.source_database_size,
        "source_database_sha256": operation.source_database_sha256,
        "backup_kind": operation.backup_kind.value,
        "expected_database_size_bytes": operation.expected_database_size,
        "expected_database_sha256": operation.expected_database_sha256,
        "expected_alembic_revision": operation.expected_alembic_revision,
        "original_files": {
            key: _stored_file_payload(stored)
            for key, stored in zip(FILE_KEYS, operation.original_files, strict=True)
        },
    }


def load_pending(paths: RestorePaths) -> PendingRestore:
    """Parse and validate the complete bounded pending metadata document."""
    parsed = _read_json(paths.pending, MAX_PENDING_BYTES, PendingRestoreCorruptError)
    if set(parsed) != PENDING_FIELDS:
        raise PendingRestoreCorruptError(
            "Pending restore metadata fields are incomplete or unexpected."
        )
    if (
        _required_int(parsed, "format_version", PendingRestoreCorruptError)
        != PENDING_FORMAT_VERSION
    ):
        raise PendingRestoreCorruptError("Pending restore metadata version is not supported.")
    request_id = _required_uuid(parsed, "request_id", PendingRestoreCorruptError)
    staged_filename = _required_filename_field(
        parsed,
        "staged_database_filename",
        PendingRestoreCorruptError,
    )
    if staged_filename != f"staged-{request_id}.sqlite":
        raise PendingRestoreCorruptError("Pending restore staged filename is invalid.")
    return PendingRestore(
        request_id=request_id,
        created_at=_required_utc(
            parsed,
            "created_at_utc",
            PendingRestoreCorruptError,
        ),
        staged_database_filename=staged_filename,
        source_backup_filename=_required_backup_filename_field(
            parsed,
            "source_backup_filename",
            PendingRestoreCorruptError,
        ),
        source_backup_sha256=_required_sha256(
            parsed,
            "source_backup_sha256",
            PendingRestoreCorruptError,
        ),
        source_application_version=_required_text(
            parsed,
            "source_application_version",
            PendingRestoreCorruptError,
        ),
        source_alembic_revision=_required_text(
            parsed,
            "source_alembic_revision",
            PendingRestoreCorruptError,
        ),
        source_database_size=_required_size(
            parsed,
            "source_database_size_bytes",
            PendingRestoreCorruptError,
        ),
        source_database_sha256=_required_sha256(
            parsed,
            "source_database_sha256",
            PendingRestoreCorruptError,
        ),
        backup_kind=_required_backup_kind(parsed, PendingRestoreCorruptError),
        expected_database_size=_required_size(
            parsed,
            "expected_database_size_bytes",
            PendingRestoreCorruptError,
        ),
        expected_database_sha256=_required_sha256(
            parsed,
            "expected_database_sha256",
            PendingRestoreCorruptError,
        ),
        expected_alembic_revision=_required_text(
            parsed,
            "expected_alembic_revision",
            PendingRestoreCorruptError,
        ),
    )


def load_operation(paths: RestorePaths) -> RestoreOperation:
    """Parse and validate the complete bounded operation journal."""
    parsed = _read_json(paths.operation, MAX_OPERATION_BYTES, RestoreRecoveryError)
    if set(parsed) != OPERATION_FIELDS:
        raise RestoreRecoveryError("Restore operation journal fields are incomplete or unexpected.")
    if _required_int(parsed, "format_version", RestoreRecoveryError) != OPERATION_FORMAT_VERSION:
        raise RestoreRecoveryError("Restore operation journal version is not supported.")
    request_id = _required_uuid(parsed, "request_id", RestoreRecoveryError)
    target_filename = _required_filename_field(
        parsed,
        "target_database_filename",
        RestoreRecoveryError,
    )
    if target_filename != paths.target.name:
        raise RestoreRecoveryError("Restore operation target does not match current settings.")
    staged_filename = _required_filename_field(
        parsed,
        "staged_database_filename",
        RestoreRecoveryError,
    )
    rollback_filename = _required_filename_field(
        parsed,
        "rollback_database_filename",
        RestoreRecoveryError,
    )
    if (
        staged_filename != f"staged-{request_id}.sqlite"
        or rollback_filename != f"rollback-{request_id}.sqlite"
    ):
        raise RestoreRecoveryError("Restore operation filenames are invalid.")
    state_text = _required_text(parsed, "state", RestoreRecoveryError)
    try:
        state = RestoreOperationState(state_text)
    except ValueError as error:
        raise RestoreRecoveryError("Restore operation state is not supported.") from error
    return RestoreOperation(
        request_id=request_id,
        state=state,
        created_at=_required_utc(
            parsed,
            "created_at_utc",
            RestoreRecoveryError,
        ),
        updated_at=_required_utc(
            parsed,
            "updated_at_utc",
            RestoreRecoveryError,
        ),
        target_database_filename=target_filename,
        staged_database_filename=staged_filename,
        rollback_database_filename=rollback_filename,
        pre_restore_backup_filename=_optional_filename_field(
            parsed,
            "pre_restore_backup_filename",
            RestoreRecoveryError,
        ),
        source_backup_filename=_required_backup_filename_field(
            parsed,
            "source_backup_filename",
            RestoreRecoveryError,
        ),
        source_application_version=_required_text(
            parsed,
            "source_application_version",
            RestoreRecoveryError,
        ),
        source_alembic_revision=_required_text(
            parsed,
            "source_alembic_revision",
            RestoreRecoveryError,
        ),
        source_database_size=_required_size(
            parsed,
            "source_database_size_bytes",
            RestoreRecoveryError,
        ),
        source_database_sha256=_required_sha256(
            parsed,
            "source_database_sha256",
            RestoreRecoveryError,
        ),
        backup_kind=_required_backup_kind(parsed, RestoreRecoveryError),
        expected_database_size=_required_size(
            parsed,
            "expected_database_size_bytes",
            RestoreRecoveryError,
        ),
        expected_database_sha256=_required_sha256(
            parsed,
            "expected_database_sha256",
            RestoreRecoveryError,
        ),
        expected_alembic_revision=_required_text(
            parsed,
            "expected_alembic_revision",
            RestoreRecoveryError,
        ),
        original_files=_parse_original_files(parsed, paths),
    )


def require_matching_pending(
    pending: PendingRestore,
    operation: RestoreOperation,
) -> None:
    """Require request identity and all durable database identities to match."""
    if pending.request_id != operation.request_id:
        raise RestoreRecoveryError(
            "Pending restore and operation journal identifiers do not match."
        )
    if (
        pending.staged_database_filename != operation.staged_database_filename
        or pending.source_backup_filename != operation.source_backup_filename
        or pending.source_application_version != operation.source_application_version
        or pending.source_alembic_revision != operation.source_alembic_revision
        or pending.source_database_size != operation.source_database_size
        or not hmac.compare_digest(
            pending.source_database_sha256,
            operation.source_database_sha256,
        )
        or pending.backup_kind is not operation.backup_kind
        or pending.expected_database_size != operation.expected_database_size
        or not hmac.compare_digest(
            pending.expected_database_sha256,
            operation.expected_database_sha256,
        )
        or pending.expected_alembic_revision != operation.expected_alembic_revision
    ):
        raise RestoreRecoveryError("Pending restore and operation journal contents do not match.")


def write_pending(
    paths: RestorePaths,
    pending: PendingRestore,
    *,
    failpoint: RestoreFailpointCallback | None = None,
) -> None:
    """Durably publish pending metadata without overwriting an existing request."""
    temporary = _write_json_temporary(
        paths.pending,
        pending.request_id,
        pending_payload(pending),
        RestoreStagingError,
    )
    reach_failpoint(
        failpoint,
        RestoreFailpoint.PENDING_METADATA_TEMPORARY_WRITTEN,
    )
    _install_json_exclusive(paths.pending, temporary)
    sync_directory_best_effort(paths.workspace)
    reach_failpoint(failpoint, RestoreFailpoint.PENDING_METADATA_REPLACED)


def write_operation_exclusive(
    paths: RestorePaths,
    operation: RestoreOperation,
) -> None:
    """Durably create the initial PREPARED journal without overwriting evidence."""
    temporary = _write_json_temporary(
        paths.operation,
        operation.request_id,
        operation_payload(operation),
        RestoreApplicationError,
    )
    try:
        _install_json_exclusive(
            paths.operation,
            temporary,
            already_exists_error=RestoreRecoveryError(
                "A restore operation journal already exists."
            ),
            installation_error=RestoreApplicationError(
                "Restore operation journal could not be installed atomically."
            ),
        )
        sync_directory_best_effort(paths.workspace)
    except RestoreError:
        raise


def replace_operation(
    paths: RestorePaths,
    operation: RestoreOperation,
) -> None:
    """Durably replace the operation journal for one validated transition."""
    if not path_present(paths.operation) or is_link_like(paths.operation):
        raise RestoreRecoveryError("Restore operation journal is missing or is a link.")
    temporary = _write_json_temporary(
        paths.operation,
        operation.request_id,
        operation_payload(operation),
        RestoreApplicationError,
    )
    try:
        os.replace(temporary, paths.operation)
        sync_directory_best_effort(paths.workspace)
    except OSError as error:
        _best_effort_unlink(temporary)
        raise RestoreApplicationError(
            "Restore operation journal could not be updated atomically."
        ) from error


def require_utc(
    value: datetime,
    error_type: type[RestoreError],
) -> datetime:
    """Require a timezone-aware UTC timestamp."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise error_type("Restore operations require a timezone-aware UTC clock.")
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Serialize one canonical timezone-aware UTC timestamp."""
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace(
            "+00:00",
            "Z",
        )
    )


def _stored_file_payload(stored: StoredFile | None) -> dict[str, object] | None:
    if stored is None:
        return None
    return {
        "filename": stored.filename,
        "size_bytes": stored.size,
        "modified_ns": stored.modified_ns,
        "sha256": stored.sha256,
    }


def _parse_original_files(
    parsed: Mapping[str, object],
    paths: RestorePaths,
) -> OriginalFiles:
    raw = parsed["original_files"]
    if not isinstance(raw, dict) or set(raw) != set(FILE_KEYS):
        raise RestoreRecoveryError("Restore original-file fingerprints are invalid.")
    expected_names = (
        paths.target.name,
        f"{paths.target.name}-wal",
        f"{paths.target.name}-shm",
        f"{paths.target.name}-journal",
    )
    stored = tuple(
        _parse_stored_file(raw[key], expected_name)
        for key, expected_name in zip(FILE_KEYS, expected_names, strict=True)
    )
    return (stored[0], stored[1], stored[2], stored[3])


def _parse_stored_file(
    value: object,
    expected_filename: str,
) -> StoredFile | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != STORED_FILE_FIELDS:
        raise RestoreRecoveryError("A restore file fingerprint is invalid.")
    filename = _required_filename_field(
        value,
        "filename",
        RestoreRecoveryError,
    )
    if filename != expected_filename:
        raise RestoreRecoveryError("A restore file fingerprint filename is invalid.")
    return StoredFile(
        filename=filename,
        size=_required_nonnegative_int(
            value,
            "size_bytes",
            RestoreRecoveryError,
        ),
        modified_ns=_required_nonnegative_int(
            value,
            "modified_ns",
            RestoreRecoveryError,
        ),
        sha256=_required_sha256(value, "sha256", RestoreRecoveryError),
    )


def _read_json(
    path: Path,
    maximum: int,
    error_type: type[RestoreError],
) -> dict[str, object]:
    if is_link_like(path) or not path.is_file():
        raise error_type("Restore metadata is not a regular file.")
    try:
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise error_type("Restore metadata exceeds the supported size.")
        parsed: object = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except RestoreError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise error_type("Restore metadata is not valid strict JSON.") from error
    if not isinstance(parsed, dict):
        raise error_type("Restore metadata must be a JSON object.")
    return parsed


def _write_json_temporary(
    destination: Path,
    request_id: UUID,
    payload: Mapping[str, object],
    error_type: type[RestoreError],
) -> Path:
    _require_metadata_destination(destination)
    data = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.{request_id}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            path = Path(temporary.name)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        set_private_permissions(path)
        return path
    except OSError as error:
        _best_effort_unlink(path)
        raise error_type("Restore metadata could not be written safely.") from error


def _install_json_exclusive(
    destination: Path,
    temporary: Path,
    *,
    already_exists_error: RestoreError | None = None,
    installation_error: RestoreError | None = None,
) -> None:
    """Reserve the final name exclusively, then atomically replace the reservation."""
    descriptor: int | None = None
    reserved = False
    try:
        descriptor = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        reserved = True
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, destination)
        reserved = False
    except FileExistsError as error:
        _best_effort_unlink(temporary)
        raise (
            already_exists_error or RestoreAlreadyPendingError("Restore metadata already exists.")
        ) from error
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        if reserved:
            _best_effort_unlink(destination)
        _best_effort_unlink(temporary)
        raise (
            installation_error
            or RestoreStagingError("Restore metadata could not be installed atomically.")
        ) from error


def _require_metadata_destination(path: Path) -> None:
    if path.name not in {PENDING_FILENAME, OPERATION_FILENAME}:
        raise RestoreStagingError("Restore metadata destination is not trusted.")
    require_restore_workspace(path.parent)


def _required_text(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> str:
    value = values[field]
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_LENGTH:
        raise error_type(f"Restore metadata field {field!r} is invalid.")
    return value


def _required_filename_field(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> str:
    value = _required_text(values, field, error_type)
    try:
        require_filename(value, field)
    except RestoreVerificationError as error:
        raise error_type(str(error)) from error
    return value


def _optional_filename_field(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> str | None:
    value = values[field]
    if value is None:
        return None
    if not isinstance(value, str):
        raise error_type(f"Restore metadata field {field!r} is invalid.")
    return _required_filename_field(values, field, error_type)


def _required_backup_filename_field(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> str:
    value = _required_filename_field(values, field, error_type)
    if Path(value).suffix != BACKUP_EXTENSION:
        raise error_type("Restore backup filename has an invalid extension.")
    return value


def _required_uuid(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> UUID:
    value = _required_text(values, field, error_type)
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise error_type("Restore request identifier is invalid.") from error
    if str(parsed) != value:
        raise error_type("Restore request identifier is not canonical.")
    return parsed


def _required_int(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> int:
    value = values[field]
    if type(value) is not int:
        raise error_type(f"Restore metadata field {field!r} must be an integer.")
    return value


def _required_nonnegative_int(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> int:
    value = _required_int(values, field, error_type)
    if value < 0:
        raise error_type(f"Restore metadata field {field!r} is invalid.")
    return value


def _required_size(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> int:
    value = _required_int(values, field, error_type)
    if value <= 0 or value > MAX_DATABASE_BYTES:
        raise error_type(f"Restore metadata field {field!r} is invalid.")
    return value


def _required_sha256(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> str:
    value = _required_text(values, field, error_type)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise error_type(f"Restore metadata field {field!r} is invalid.")
    return value


def _required_backup_kind(
    values: Mapping[str, object],
    error_type: type[RestoreError],
) -> BackupKind:
    value = _required_text(values, "backup_kind", error_type)
    try:
        return BackupKind(value)
    except ValueError as error:
        raise error_type("Restore backup kind is invalid.") from error


def _required_utc(
    values: Mapping[str, object],
    field: str,
    error_type: type[RestoreError],
) -> datetime:
    value = _required_text(values, field, error_type)
    if not value.endswith("Z"):
        raise error_type("Restore metadata timestamp must use UTC.")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise error_type("Restore metadata timestamp is invalid.") from error
    parsed = require_utc(parsed, error_type)
    if format_utc(parsed) != value:
        raise error_type("Restore metadata timestamp is not canonical UTC.")
    return parsed


def _unique_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Never:
    raise ValueError(f"Non-standard JSON constant {value!r}.")


def _best_effort_unlink(path: Path | None) -> None:
    if path is None or is_link_like(path):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "MAX_OPERATION_BYTES",
    "MAX_PENDING_BYTES",
    "OPERATION_FORMAT_VERSION",
    "PENDING_FORMAT_VERSION",
    "UNVERSIONED_REVISION",
    "OriginalFiles",
    "PendingRestore",
    "RestoreOperation",
    "StoredFile",
    "format_utc",
    "load_operation",
    "load_pending",
    "operation_payload",
    "pending_payload",
    "replace_operation",
    "require_matching_pending",
    "require_utc",
    "write_operation_exclusive",
    "write_pending",
]
