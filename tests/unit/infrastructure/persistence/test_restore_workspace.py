"""Ownership, confinement, link rejection, and cleanup tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from app.core.exceptions import (
    PendingRestoreCorruptError,
    RestoreRecoveryError,
    RestoreVerificationError,
)
from app.infrastructure.persistence.restore_workspace import (
    RestorePaths,
    confined_filename,
    remove_owned_database,
    require_regular_owned_database,
    rollback_database_path,
    staged_database_path,
)
from app.infrastructure.persistence.sqlite_validation import sqlite_sidecar_paths

REQUEST_ID = UUID("12345678-1234-5678-9234-567812345678")
OTHER_ID = UUID("87654321-4321-8765-9876-876543218765")


def make_paths(tmp_path: Path) -> RestorePaths:
    database_directory = tmp_path / "database"
    database_directory.mkdir()
    workspace = database_directory / "restore"
    workspace.mkdir()
    return RestorePaths(
        target=database_directory / "portfolio.db",
        workspace=workspace,
        pending=workspace / "pending.json",
        operation=workspace / "operation.json",
    )


@pytest.mark.parametrize(
    "filename",
    (
        "../staged.sqlite",
        "..\\staged.sqlite",
        "/absolute.sqlite",
        "nested/staged.sqlite",
        "..",
    ),
)
def test_confined_filename_rejects_traversal_and_non_basenames(
    tmp_path: Path,
    filename: str,
) -> None:
    paths = make_paths(tmp_path)

    with pytest.raises(RestoreVerificationError, match="filename"):
        confined_filename(paths.workspace, filename)


def test_owned_database_names_require_the_exact_canonical_request_uuid(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)

    with pytest.raises(PendingRestoreCorruptError, match="staged filename"):
        staged_database_path(
            paths,
            REQUEST_ID,
            f"staged-{OTHER_ID}.sqlite",
        )
    with pytest.raises(RestoreRecoveryError, match="rollback filename"):
        rollback_database_path(
            paths,
            REQUEST_ID,
            f"rollback-{OTHER_ID}.sqlite",
        )


def test_cleanup_removes_only_explicit_owned_files_without_recursing(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    owned = staged_database_path(
        paths,
        REQUEST_ID,
        f"staged-{REQUEST_ID}.sqlite",
    )
    owned.path.write_bytes(b"owned")
    for sidecar in sqlite_sidecar_paths(owned.path):
        sidecar.write_bytes(b"owned sidecar")
    unrelated = paths.workspace / "unrelated.sqlite"
    unrelated.write_bytes(b"unrelated")
    nested = paths.workspace / "nested"
    nested.mkdir()
    nested_file = nested / "keep.sqlite"
    nested_file.write_bytes(b"nested")

    remove_owned_database(owned, RestoreRecoveryError)

    assert not owned.path.exists()
    assert all(not sidecar.exists() for sidecar in sqlite_sidecar_paths(owned.path))
    assert unrelated.read_bytes() == b"unrelated"
    assert nested_file.read_bytes() == b"nested"


def test_symlinked_staged_database_is_rejected_without_following(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    outside = tmp_path / "outside.sqlite"
    outside.write_bytes(b"outside")
    owned = staged_database_path(
        paths,
        REQUEST_ID,
        f"staged-{REQUEST_ID}.sqlite",
    )
    try:
        owned.path.symlink_to(outside)
    except OSError:
        pytest.skip("Filesystem symlink creation is unavailable")

    with pytest.raises(RestoreVerificationError):
        require_regular_owned_database(
            owned,
            RestoreVerificationError("link rejected"),
        )
    with pytest.raises(RestoreRecoveryError, match="link"):
        remove_owned_database(owned, RestoreRecoveryError)

    assert outside.read_bytes() == b"outside"
