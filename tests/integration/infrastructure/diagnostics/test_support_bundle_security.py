"""Adversarial verification tests for the support bundle archive boundary."""

from __future__ import annotations

import hashlib
import json
import stat
import warnings
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from loguru import logger

from app.core import config, runtime_paths
from app.core.exceptions import SupportBundleVerificationError
from app.core.settings import Settings
from app.infrastructure.database import DatabaseManager
from app.infrastructure.diagnostics import (
    SupportBundleDiagnosticsService,
    verify_support_bundle,
)
from app.infrastructure.persistence.database_preparation import prepare_database


@pytest.fixture(autouse=True)
def close_loguru_sinks() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


@pytest.fixture
def diagnostics_settings(tmp_path: Path) -> Settings:
    root = tmp_path / "security"
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
def diagnostics_service(
    diagnostics_settings: Settings,
) -> Iterator[SupportBundleDiagnosticsService]:
    manager = DatabaseManager(diagnostics_settings).initialize()
    yield SupportBundleDiagnosticsService(
        diagnostics_settings,
        manager,
        clock=lambda: datetime(2026, 7, 30, 18, 25, tzinfo=UTC),
        uuid_factory=lambda: UUID("12345678-1234-4234-8234-123456789abc"),
    )
    manager.shutdown()


@pytest.fixture
def valid_bundle(
    diagnostics_service: SupportBundleDiagnosticsService,
) -> Path:
    return diagnostics_service.create_support_bundle().path


def parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def member_info(
    name: str,
    *,
    mode: int = stat.S_IFREG | 0o600,
) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2026, 7, 30, 18, 25, 0))
    info.create_system = 3
    info.external_attr = mode << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    info.filename = name
    return info


def write_archive(path: Path, members: list[tuple[zipfile.ZipInfo, bytes]]) -> Path:
    with zipfile.ZipFile(path, mode="x") as archive:
        for info, content in members:
            archive.writestr(info, content)
    return path


def valid_rewrite_name(tmp_path: Path, suffix: str) -> Path:
    digest = hashlib.sha256(suffix.encode()).hexdigest()[:12]
    return tmp_path / f"mira-portfolio-support-20260730T182500Z-{digest}.mirasupport"


def rewrite(
    tmp_path: Path,
    valid_bundle: Path,
    *,
    content: dict[str, bytes] | None = None,
    suffix: str,
) -> Path:
    rewritten = content if content is not None else parts(valid_bundle)
    return write_archive(
        valid_rewrite_name(tmp_path, suffix),
        [(member_info(name), value) for name, value in rewritten.items()],
    )


def manifest(content: dict[str, bytes]) -> dict[str, object]:
    parsed = json.loads(content["manifest.json"].decode("utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def encode_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def update_manifest_member(
    content: dict[str, bytes],
    member_name: str,
) -> None:
    payload = manifest(content)
    entries = payload["members"]
    assert isinstance(entries, list)
    entry = next(item for item in entries if item["name"] == member_name)
    entry["size_bytes"] = len(content[member_name])
    entry["sha256"] = hashlib.sha256(content[member_name]).hexdigest()
    content["manifest.json"] = encode_json(payload)


def test_invalid_zip_is_rejected(
    diagnostics_settings: Settings,
    tmp_path: Path,
) -> None:
    path = valid_rewrite_name(tmp_path, "invalid")
    path.write_bytes(b"not zip")

    with pytest.raises(SupportBundleVerificationError, match="verification failed"):
        verify_support_bundle(diagnostics_settings, path)


@pytest.mark.parametrize("missing", ("manifest.json", "database.json"))
def test_missing_core_member_is_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
    missing: str,
) -> None:
    content = parts(valid_bundle)
    del content[missing]
    path = rewrite(tmp_path, valid_bundle, content=content, suffix=f"missing-{missing}")

    with pytest.raises(SupportBundleVerificationError, match="incomplete"):
        verify_support_bundle(diagnostics_settings, path)


def test_extra_member_is_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    content = parts(valid_bundle)
    content["extra.txt"] = b"unexpected"
    path = rewrite(tmp_path, valid_bundle, content=content, suffix="extra")

    with pytest.raises(SupportBundleVerificationError, match="unexpected"):
        verify_support_bundle(diagnostics_settings, path)


def test_duplicate_member_is_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    content = parts(valid_bundle)
    path = valid_rewrite_name(tmp_path, "duplicate")
    members = [(member_info(name), value) for name, value in content.items()]
    members.append((member_info("database.json"), content["database.json"]))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        write_archive(path, members)

    with pytest.raises(SupportBundleVerificationError, match="duplicate"):
        verify_support_bundle(diagnostics_settings, path)


@pytest.mark.parametrize(
    "unsafe_name",
    ("/logs/log-1.log", "../logs/log-1.log", r"logs\log-1.log"),
)
def test_absolute_traversal_and_backslash_names_are_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    content = parts(valid_bundle)
    content[unsafe_name] = b"unsafe"
    path = rewrite(tmp_path, valid_bundle, content=content, suffix=unsafe_name)

    with pytest.raises(
        SupportBundleVerificationError,
        match="unsafe|does not match archive members",
    ):
        verify_support_bundle(diagnostics_settings, path)


@pytest.mark.parametrize(
    "mode",
    (stat.S_IFDIR | 0o700, stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600),
)
def test_directory_symlink_and_special_members_are_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
    mode: int,
) -> None:
    content = parts(valid_bundle)
    path = valid_rewrite_name(tmp_path, f"mode-{mode}")
    members = [
        (
            member_info(name, mode=mode if name == "application.json" else stat.S_IFREG | 0o600),
            value,
        )
        for name, value in content.items()
    ]
    write_archive(path, members)

    with pytest.raises(SupportBundleVerificationError, match="non-regular"):
        verify_support_bundle(diagnostics_settings, path)


def test_invalid_and_duplicate_json_are_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    invalid = parts(valid_bundle)
    invalid["manifest.json"] = b"{not json"
    invalid_path = rewrite(tmp_path, valid_bundle, content=invalid, suffix="invalid-json")
    with pytest.raises(SupportBundleVerificationError, match="JSON is invalid"):
        verify_support_bundle(diagnostics_settings, invalid_path)

    duplicate = parts(valid_bundle)
    duplicate["manifest.json"] = b'{"format_version":1,"format_version":1,"bundle_id":"x"}'
    duplicate_path = rewrite(
        tmp_path,
        valid_bundle,
        content=duplicate,
        suffix="duplicate-json",
    )
    with pytest.raises(SupportBundleVerificationError, match="JSON is invalid"):
        verify_support_bundle(diagnostics_settings, duplicate_path)


def test_unsupported_format_version_is_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    content = parts(valid_bundle)
    payload = manifest(content)
    payload["format_version"] = 999
    content["manifest.json"] = encode_json(payload)
    path = rewrite(tmp_path, valid_bundle, content=content, suffix="format")

    with pytest.raises(SupportBundleVerificationError, match="not supported"):
        verify_support_bundle(diagnostics_settings, path)


def test_tampered_member_hash_and_size_are_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    tampered = parts(valid_bundle)
    tampered["application.json"] += b" "
    tampered_path = rewrite(
        tmp_path,
        valid_bundle,
        content=tampered,
        suffix="tampered-hash",
    )
    with pytest.raises(SupportBundleVerificationError, match="size does not match"):
        verify_support_bundle(diagnostics_settings, tampered_path)

    wrong_size = parts(valid_bundle)
    payload = manifest(wrong_size)
    entries = payload["members"]
    assert isinstance(entries, list)
    entry = next(item for item in entries if item["name"] == "application.json")
    entry["size_bytes"] += 1
    wrong_size["manifest.json"] = encode_json(payload)
    wrong_size_path = rewrite(
        tmp_path,
        valid_bundle,
        content=wrong_size,
        suffix="wrong-size",
    )
    with pytest.raises(SupportBundleVerificationError, match="size does not match"):
        verify_support_bundle(diagnostics_settings, wrong_size_path)


def test_tampered_hash_with_matching_size_is_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    content = parts(valid_bundle)
    payload = manifest(content)
    entries = payload["members"]
    assert isinstance(entries, list)
    entry = next(item for item in entries if item["name"] == "system.json")
    entry["sha256"] = "0" * 64
    content["manifest.json"] = encode_json(payload)
    path = rewrite(tmp_path, valid_bundle, content=content, suffix="wrong-hash")

    with pytest.raises(SupportBundleVerificationError, match="hash does not match"):
        verify_support_bundle(diagnostics_settings, path)


def test_core_field_tampering_is_rejected_even_with_updated_hash(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    content = parts(valid_bundle)
    application = json.loads(content["application.json"])
    application["unexpected"] = True
    content["application.json"] = encode_json(application)
    update_manifest_member(content, "application.json")
    path = rewrite(tmp_path, valid_bundle, content=content, suffix="fields")

    with pytest.raises(SupportBundleVerificationError, match="fields are invalid"):
        verify_support_bundle(diagnostics_settings, path)


def test_core_type_tampering_is_rejected_even_with_updated_hash(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    content = parts(valid_bundle)
    application = json.loads(content["application.json"])
    application["debug"] = "yes"
    content["application.json"] = encode_json(application)
    update_manifest_member(content, "application.json")
    path = rewrite(tmp_path, valid_bundle, content=content, suffix="types")

    with pytest.raises(
        SupportBundleVerificationError,
        match="application diagnostics are invalid",
    ):
        verify_support_bundle(diagnostics_settings, path)


def test_nonfinite_json_number_is_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    content = parts(valid_bundle)
    content["application.json"] = content["application.json"].replace(
        b'"debug":false',
        b'"debug":NaN',
    )
    update_manifest_member(content, "application.json")
    path = rewrite(tmp_path, valid_bundle, content=content, suffix="nan")

    with pytest.raises(SupportBundleVerificationError, match="JSON is invalid"):
        verify_support_bundle(diagnostics_settings, path)


def test_core_members_cannot_claim_truncation(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    content = parts(valid_bundle)
    payload = manifest(content)
    entries = payload["members"]
    assert isinstance(entries, list)
    entry = next(item for item in entries if item["name"] == "application.json")
    entry["truncated"] = True
    content["manifest.json"] = encode_json(payload)
    path = rewrite(tmp_path, valid_bundle, content=content, suffix="core-truncated")

    with pytest.raises(SupportBundleVerificationError, match="Only support bundle log"):
        verify_support_bundle(diagnostics_settings, path)


def test_private_path_is_rejected_even_with_updated_hash(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    content = parts(valid_bundle)
    application = json.loads(content["application.json"])
    application["environment"] = str(diagnostics_settings.data_directory)
    content["application.json"] = encode_json(application)
    update_manifest_member(content, "application.json")
    path = rewrite(tmp_path, valid_bundle, content=content, suffix="privacy")

    with pytest.raises(SupportBundleVerificationError, match="privacy"):
        verify_support_bundle(diagnostics_settings, path)


def test_archive_and_member_limits_are_enforced_before_unbounded_reads(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SUPPORT_BUNDLE_MAX_ARCHIVE_BYTES", 1)

    with pytest.raises(SupportBundleVerificationError, match="archive limit"):
        verify_support_bundle(diagnostics_settings, valid_bundle)


def test_invalid_filename_and_symlinked_archive_are_rejected(
    diagnostics_settings: Settings,
    valid_bundle: Path,
    tmp_path: Path,
) -> None:
    wrong = tmp_path / "wrong.mirasupport"
    wrong.write_bytes(valid_bundle.read_bytes())
    with pytest.raises(SupportBundleVerificationError, match="filename"):
        verify_support_bundle(diagnostics_settings, wrong)

    link = valid_rewrite_name(tmp_path, "link")
    try:
        link.symlink_to(valid_bundle)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")
    with pytest.raises(SupportBundleVerificationError, match="regular file"):
        verify_support_bundle(diagnostics_settings, link)
