"""Adversarial verification tests for the strict backup archive format."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import warnings
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.exceptions import BackupVerificationError
from app.core.settings import Settings
from app.infrastructure.persistence.database_backup import (
    DATABASE_MEMBER,
    MANIFEST_MEMBER,
    MAX_DATABASE_BYTES,
    MAX_MANIFEST_BYTES,
    SQLiteBackupService,
)


@pytest.fixture(autouse=True)
def verification_files_are_always_removed(tmp_path: Path) -> Iterator[None]:
    yield
    assert list(tmp_path.rglob(".verify-*")) == []


@pytest.fixture
def valid_archive(backup_service: SQLiteBackupService) -> Path:
    return backup_service.create_backup().path


def archive_parts(path: Path) -> tuple[dict[str, object], bytes]:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read(MANIFEST_MEMBER).decode("utf-8"))
        database = archive.read(DATABASE_MEMBER)
    assert isinstance(manifest, dict)
    return manifest, database


def member_info(
    name: str,
    *,
    mode: int = stat.S_IFREG | 0o600,
) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = mode << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def manifest_bytes(manifest: dict[str, object]) -> bytes:
    return json.dumps(
        manifest,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def write_archive(
    path: Path,
    members: list[tuple[zipfile.ZipInfo, bytes]],
) -> Path:
    with zipfile.ZipFile(path, "x") as archive:
        for info, data in members:
            archive.writestr(info, data)
    return path


def rewritten_archive(
    tmp_path: Path,
    valid_archive: Path,
    *,
    manifest: dict[str, object] | None = None,
    database: bytes | None = None,
    filename: str = "rewritten.mirabackup",
) -> Path:
    original_manifest, original_database = archive_parts(valid_archive)
    return write_archive(
        tmp_path / filename,
        [
            (
                member_info(MANIFEST_MEMBER),
                manifest_bytes(manifest if manifest is not None else original_manifest),
            ),
            (
                member_info(DATABASE_MEMBER),
                database if database is not None else original_database,
            ),
        ],
    )


def update_database_manifest(
    manifest: dict[str, object],
    database: bytes,
) -> dict[str, object]:
    updated = dict(manifest)
    updated["database_size_bytes"] = len(database)
    updated["database_sha256"] = hashlib.sha256(database).hexdigest()
    return updated


def test_invalid_zip_fails_verification(
    backup_service: SQLiteBackupService,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "invalid.mirabackup"
    archive.write_bytes(b"not a zip archive")

    with pytest.raises(BackupVerificationError, match="verification failed"):
        backup_service.verify_backup(archive)


@pytest.mark.parametrize(
    ("members_to_keep", "message"),
    (
        ((DATABASE_MEMBER,), "incomplete"),
        ((MANIFEST_MEMBER,), "incomplete"),
    ),
)
def test_missing_required_member_fails(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
    members_to_keep: tuple[str, ...],
    message: str,
) -> None:
    manifest, database = archive_parts(valid_archive)
    payloads = {
        MANIFEST_MEMBER: manifest_bytes(manifest),
        DATABASE_MEMBER: database,
    }
    archive = write_archive(
        tmp_path / f"missing-{members_to_keep[0]}.mirabackup",
        [(member_info(name), payloads[name]) for name in members_to_keep],
    )

    with pytest.raises(BackupVerificationError, match=message):
        backup_service.verify_backup(archive)


def test_extra_archive_member_fails(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
) -> None:
    manifest, database = archive_parts(valid_archive)
    archive = write_archive(
        tmp_path / "extra.mirabackup",
        [
            (member_info(MANIFEST_MEMBER), manifest_bytes(manifest)),
            (member_info(DATABASE_MEMBER), database),
            (member_info("extra.txt"), b"unexpected"),
        ],
    )

    with pytest.raises(BackupVerificationError, match="too many"):
        backup_service.verify_backup(archive)


def test_duplicate_member_fails(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
) -> None:
    manifest, database = archive_parts(valid_archive)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        archive = write_archive(
            tmp_path / "duplicate.mirabackup",
            [
                (member_info(MANIFEST_MEMBER), manifest_bytes(manifest)),
                (member_info(DATABASE_MEMBER), database),
                (member_info(DATABASE_MEMBER), database),
            ],
        )

    with pytest.raises(BackupVerificationError, match="too many|duplicate"):
        backup_service.verify_backup(archive)


@pytest.mark.parametrize(
    "unsafe_name",
    (
        "/database.sqlite",
        "../database.sqlite",
        "folder\\database.sqlite",
    ),
)
def test_absolute_traversal_or_backslash_member_fails(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    manifest, database = archive_parts(valid_archive)
    archive = write_archive(
        tmp_path / f"unsafe-{len(unsafe_name)}.mirabackup",
        [
            (member_info(MANIFEST_MEMBER), manifest_bytes(manifest)),
            (member_info(unsafe_name), database),
        ],
    )

    with pytest.raises(BackupVerificationError, match="unsafe member path"):
        backup_service.verify_backup(archive)


@pytest.mark.parametrize(
    "mode",
    (stat.S_IFDIR | 0o700, stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600),
)
def test_symlink_or_special_member_fails(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
    mode: int,
) -> None:
    manifest, database = archive_parts(valid_archive)
    archive = write_archive(
        tmp_path / f"special-{mode}.mirabackup",
        [
            (member_info(MANIFEST_MEMBER), manifest_bytes(manifest)),
            (member_info(DATABASE_MEMBER, mode=mode), database),
        ],
    )

    with pytest.raises(BackupVerificationError, match="non-regular"):
        backup_service.verify_backup(archive)


def test_invalid_json_fails(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
) -> None:
    _, database = archive_parts(valid_archive)
    archive = write_archive(
        tmp_path / "invalid-json.mirabackup",
        [
            (member_info(MANIFEST_MEMBER), b"{invalid"),
            (member_info(DATABASE_MEMBER), database),
        ],
    )

    with pytest.raises(BackupVerificationError, match="strict JSON"):
        backup_service.verify_backup(archive)


def test_duplicate_json_keys_fail(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
) -> None:
    _, database = archive_parts(valid_archive)
    archive = write_archive(
        tmp_path / "duplicate-json.mirabackup",
        [
            (
                member_info(MANIFEST_MEMBER),
                b'{"format_version":1,"format_version":1}',
            ),
            (member_info(DATABASE_MEMBER), database),
        ],
    )

    with pytest.raises(BackupVerificationError, match="strict JSON"):
        backup_service.verify_backup(archive)


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_nonstandard_json_numeric_constants_fail(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
    constant: str,
) -> None:
    manifest, database = archive_parts(valid_archive)
    original = manifest_bytes(manifest)
    declared_size = manifest["database_size_bytes"]
    invalid = original.replace(
        f'"database_size_bytes":{declared_size}'.encode(),
        f'"database_size_bytes":{constant}'.encode(),
    )
    archive = write_archive(
        tmp_path / f"constant-{constant.strip('-')}.mirabackup",
        [
            (member_info(MANIFEST_MEMBER), invalid),
            (member_info(DATABASE_MEMBER), database),
        ],
    )

    with pytest.raises(BackupVerificationError, match="strict JSON"):
        backup_service.verify_backup(archive)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("format_version", 2, "format version"),
        ("application_name", "Another Application", "different application"),
        ("application_version", "", "application version"),
        ("backup_kind", "automatic", "kind"),
        ("created_at_utc", "2026-07-30T17:25:00", "timestamp"),
        ("created_at_utc", "2026-07-30T17:25:00+03:00", "timestamp"),
        ("database_sha256", "not-a-hash", "checksum format"),
        ("database_filename", "/absolute/database.sqlite", "filename"),
    ),
)
def test_invalid_manifest_value_fails(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    manifest, _ = archive_parts(valid_archive)
    manifest[field] = value
    archive = rewritten_archive(
        tmp_path,
        valid_archive,
        manifest=manifest,
        filename=f"invalid-{field}-{len(str(value))}.mirabackup",
    )

    with pytest.raises(BackupVerificationError, match=message):
        backup_service.verify_backup(archive)


@pytest.mark.parametrize("alteration", ("missing", "extra"))
def test_missing_or_extra_manifest_field_fails(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
    alteration: str,
) -> None:
    manifest, _ = archive_parts(valid_archive)
    if alteration == "missing":
        del manifest["application_version"]
    else:
        manifest["source_path"] = "forbidden"
    archive = rewritten_archive(tmp_path, valid_archive, manifest=manifest)

    with pytest.raises(BackupVerificationError, match="fields"):
        backup_service.verify_backup(archive)


def test_oversized_manifest_fails_before_parsing(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
) -> None:
    _, database = archive_parts(valid_archive)
    archive = write_archive(
        tmp_path / "oversized-manifest.mirabackup",
        [
            (member_info(MANIFEST_MEMBER), b"x" * (MAX_MANIFEST_BYTES + 1)),
            (member_info(DATABASE_MEMBER), database),
        ],
    )

    with pytest.raises(BackupVerificationError, match="manifest exceeds"):
        backup_service.verify_backup(archive)


def test_tampered_database_payload_fails_checksum(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
) -> None:
    _, database = archive_parts(valid_archive)
    tampered = database[:-1] + bytes((database[-1] ^ 1,))
    archive = rewritten_archive(tmp_path, valid_archive, database=tampered)

    with pytest.raises(BackupVerificationError, match="checksum"):
        backup_service.verify_backup(archive)


def test_tampered_manifest_size_fails_before_extraction(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
) -> None:
    manifest, _ = archive_parts(valid_archive)
    manifest["database_size_bytes"] = int(manifest["database_size_bytes"]) + 1
    archive = rewritten_archive(tmp_path, valid_archive, manifest=manifest)

    with pytest.raises(BackupVerificationError, match="size does not match"):
        backup_service.verify_backup(archive)


def test_oversized_declared_database_fails_before_extraction(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
) -> None:
    manifest, _ = archive_parts(valid_archive)
    manifest["database_size_bytes"] = MAX_DATABASE_BYTES + 1
    archive = rewritten_archive(tmp_path, valid_archive, manifest=manifest)

    with pytest.raises(BackupVerificationError, match="size is invalid|supported size"):
        backup_service.verify_backup(archive)


def test_corrupt_sqlite_payload_with_matching_hash_fails_integrity(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
) -> None:
    manifest, _ = archive_parts(valid_archive)
    corrupt = b"not a sqlite database"
    archive = rewritten_archive(
        tmp_path,
        valid_archive,
        manifest=update_database_manifest(manifest, corrupt),
        database=corrupt,
    )

    with pytest.raises(BackupVerificationError, match="integrity"):
        backup_service.verify_backup(archive)


@pytest.mark.parametrize("mutation", ("revision", "schema"))
def test_wrong_revision_or_schema_drift_fails_database_validation(
    backup_service: SQLiteBackupService,
    valid_archive: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    manifest, database = archive_parts(valid_archive)
    database_path = tmp_path / f"{mutation}.sqlite"
    database_path.write_bytes(database)
    with sqlite3.connect(database_path) as connection:
        if mutation == "revision":
            connection.execute(
                "UPDATE alembic_version SET version_num = ?",
                ("unknown_revision",),
            )
        else:
            connection.execute("ALTER TABLE assets ADD COLUMN drifted TEXT")
    changed_database = database_path.read_bytes()
    archive = rewritten_archive(
        tmp_path,
        valid_archive,
        manifest=update_database_manifest(manifest, changed_database),
        database=changed_database,
        filename=f"{mutation}.mirabackup",
    )

    with pytest.raises(BackupVerificationError, match="revision|schema"):
        backup_service.verify_backup(archive)


def test_wrong_extension_missing_path_and_directory_fail_safely(
    backup_service: SQLiteBackupService,
    tmp_path: Path,
) -> None:
    wrong_extension = tmp_path / "backup.zip"
    wrong_extension.write_bytes(b"zip")
    missing = tmp_path / "missing.mirabackup"
    directory = tmp_path / "directory.mirabackup"
    directory.mkdir()

    with pytest.raises(BackupVerificationError, match="extension"):
        backup_service.verify_backup(wrong_extension)
    with pytest.raises(BackupVerificationError, match="does not exist"):
        backup_service.verify_backup(missing)
    with pytest.raises(BackupVerificationError, match="regular file"):
        backup_service.verify_backup(directory)


def test_archive_identity_contains_no_database_url_or_absolute_source_path(
    backup_settings: Settings,
    valid_archive: Path,
) -> None:
    with zipfile.ZipFile(valid_archive) as archive:
        assert archive.namelist() == [MANIFEST_MEMBER, DATABASE_MEMBER]
        manifest = archive.read(MANIFEST_MEMBER).decode("utf-8")

    assert backup_settings.database_url not in manifest
    assert str(backup_settings.database_path) not in manifest
    assert str(Path.home()) not in manifest
