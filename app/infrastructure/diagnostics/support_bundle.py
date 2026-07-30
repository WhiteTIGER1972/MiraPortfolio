"""Atomic creation and strict verification of privacy-safe support bundles."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Final, Never, cast
from uuid import UUID, uuid4

from loguru import logger

from app.application.diagnostics import DiagnosticsService, SupportBundleRecord
from app.core import config
from app.core.exceptions import (
    DiagnosticsError,
    SupportBundleCreationError,
    SupportBundleVerificationError,
)
from app.core.redaction import RedactionPolicy
from app.core.settings import Settings
from app.infrastructure.database import DatabaseManager
from app.infrastructure.diagnostics.collection import (
    DiagnosticCollection,
    DiagnosticCollector,
    DiagnosticMember,
)
from app.infrastructure.persistence.restore_workspace import is_link_like

type JsonValue = None | bool | int | str | list[JsonValue] | dict[str, JsonValue]

_MANIFEST_MEMBER: Final = "manifest.json"
_APPLICATION_MEMBER: Final = "application.json"
_SYSTEM_MEMBER: Final = "system.json"
_DATABASE_MEMBER: Final = "database.json"
_RUNTIME_MEMBER: Final = "runtime.json"
_CORE_MEMBERS: Final = frozenset(
    {
        _MANIFEST_MEMBER,
        _APPLICATION_MEMBER,
        _SYSTEM_MEMBER,
        _DATABASE_MEMBER,
        _RUNTIME_MEMBER,
    }
)
_NON_MANIFEST_CORE_MEMBERS: Final = frozenset(_CORE_MEMBERS - {_MANIFEST_MEMBER})
_MANIFEST_FIELDS: Final = frozenset(
    {
        "format_version",
        "bundle_id",
        "application_name",
        "application_version",
        "created_at_utc",
        "members",
    }
)
_MEMBER_FIELDS: Final = frozenset({"name", "size_bytes", "sha256", "truncated"})
_APPLICATION_FIELDS: Final = frozenset(
    {
        "application_name",
        "application_version",
        "environment",
        "debug",
        "theme",
        "language",
        "log_level",
        "default_currency",
        "auto_backup",
        "auto_snapshot",
    }
)
_SYSTEM_FIELDS: Final = frozenset(
    {
        "operating_system_family",
        "operating_system_release",
        "architecture",
        "python_implementation",
        "python_version",
        "packaged",
        "locale",
        "timezone_offset",
        "package_versions",
    }
)
_DATABASE_FIELDS: Final = frozenset(
    {
        "backend_family",
        "file_based",
        "configured_database_filename",
        "database_exists",
        "database_size_bytes",
        "health_check",
        "current_alembic_revision",
        "expected_alembic_head",
        "revision_current",
        "schema_compatible",
        "sqlite_integrity",
        "foreign_key_enforcement",
        "journal_mode",
        "status",
        "error_category",
    }
)
_RUNTIME_FIELDS: Final = frozenset(
    {
        "directories",
        "active_log_exists",
        "log_file_count",
        "log_omission_count",
        "pending_restore_state",
        "backup_archive_count",
        "invalid_backup_count",
        "backup_scan_truncated",
    }
)
_LOG_MEMBER_PATTERN: Final = re.compile(r"^logs/log-[1-5]\.log$")
_BUNDLE_FILENAME_PATTERN: Final = re.compile(
    r"^mira-portfolio-support-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\.mirasupport$"
)
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE_PATTERN: Final = re.compile(r"^[a-z0-9_+.-]+$")
_HASH_CHUNK_SIZE: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class _ManifestMember:
    name: str
    size: int
    sha256: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class _SupportManifest:
    bundle_id: UUID
    created_at: datetime
    application_name: str
    application_version: str
    members: tuple[_ManifestMember, ...]


class SupportBundleDiagnosticsService(DiagnosticsService):
    """Create strict support bundles without exposing active application data."""

    def __init__(
        self,
        settings: Settings,
        database_manager: DatabaseManager,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
        log_read_hook: Callable[[Path], None] | None = None,
    ) -> None:
        self._settings = settings
        self._database_manager = database_manager
        self._clock = clock or _utc_now
        self._uuid_factory = uuid_factory or uuid4
        self._log_read_hook = log_read_hook
        self._policy = RedactionPolicy.from_settings(settings)

    def create_support_bundle(self) -> SupportBundleRecord:
        """Collect, verify, and atomically install one privacy-safe bundle."""
        logger.info("Support bundle creation started")
        created_at = _require_utc(self._clock(), creation=True)
        bundle_id = self._uuid_factory()
        support_directory = _ensure_support_directory(self._settings)
        final_path = _bundle_path(support_directory, created_at, bundle_id)
        temporary_path: Path | None = None
        installed = False
        try:
            collection = DiagnosticCollector(
                self._settings,
                self._database_manager,
                self._policy,
                log_read_hook=self._log_read_hook,
            ).collect()
            members = _collection_members(collection)
            manifest = _manifest_for(
                self._settings,
                self._policy,
                bundle_id,
                created_at,
                members,
            )
            manifest_member = DiagnosticMember(
                name=_MANIFEST_MEMBER,
                content=_json_bytes(_manifest_payload(manifest)),
            )
            all_members = (manifest_member, *members)
            _privacy_gate(all_members, self._policy)
            temporary_path = _reserve_temporary_archive(support_directory)
            _write_archive(temporary_path, all_members, created_at)
            verified = _verify_archive(
                self._settings,
                self._policy,
                temporary_path,
                enforce_filename=False,
            )
            _install_without_overwrite(temporary_path, final_path)
            installed = True
            final_record = replace(
                verified,
                path=final_path,
                filename=final_path.name,
                archive_size_bytes=final_path.stat().st_size,
                archive_sha256=_sha256_file(final_path),
            )
            logger.info(
                "Support bundle creation completed: {}",
                final_record.filename,
            )
            return final_record
        except DiagnosticsError as error:
            logger.error(
                "Support bundle creation failed: {}",
                type(error).__name__,
            )
            if installed:
                _best_effort_unlink(final_path)
            raise
        except (OSError, UnicodeError, ValueError, zipfile.BadZipFile) as error:
            logger.error(
                "Support bundle creation failed: {}",
                type(error).__name__,
            )
            if installed:
                _best_effort_unlink(final_path)
            raise SupportBundleCreationError(
                "The support bundle could not be created safely."
            ) from error
        except Exception as error:
            logger.error(
                "Support bundle creation failed: {}",
                type(error).__name__,
            )
            if installed:
                _best_effort_unlink(final_path)
            raise SupportBundleCreationError(
                "The support bundle could not be created safely."
            ) from error
        finally:
            if temporary_path is not None:
                _best_effort_unlink(temporary_path)

    def verify_support_bundle(self, path: Path) -> SupportBundleRecord:
        """Verify a support bundle without extracting or installing it."""
        return _verify_archive(
            self._settings,
            self._policy,
            path,
            enforce_filename=True,
        )


def verify_support_bundle(settings: Settings, path: Path) -> SupportBundleRecord:
    """Independently verify a support bundle using the configured privacy policy."""
    return _verify_archive(
        settings,
        RedactionPolicy.from_settings(settings),
        path,
        enforce_filename=True,
    )


def _collection_members(
    collection: DiagnosticCollection,
) -> tuple[DiagnosticMember, ...]:
    core = (
        DiagnosticMember(
            name=_APPLICATION_MEMBER,
            content=_json_bytes(collection.application),
        ),
        DiagnosticMember(
            name=_SYSTEM_MEMBER,
            content=_json_bytes(collection.system),
        ),
        DiagnosticMember(
            name=_DATABASE_MEMBER,
            content=_json_bytes(collection.database),
        ),
        DiagnosticMember(
            name=_RUNTIME_MEMBER,
            content=_json_bytes(collection.runtime),
        ),
    )
    return (*core, *collection.logs)


def _manifest_for(
    settings: Settings,
    policy: RedactionPolicy,
    bundle_id: UUID,
    created_at: datetime,
    members: tuple[DiagnosticMember, ...],
) -> _SupportManifest:
    entries = tuple(
        sorted(
            (
                _ManifestMember(
                    name=member.name,
                    size=len(member.content),
                    sha256=hashlib.sha256(member.content).hexdigest(),
                    truncated=member.truncated,
                )
                for member in members
            ),
            key=lambda member: member.name,
        )
    )
    return _SupportManifest(
        bundle_id=bundle_id,
        created_at=created_at,
        application_name=policy.redact(settings.app_name),
        application_version=policy.redact(settings.app_version),
        members=entries,
    )


def _manifest_payload(manifest: _SupportManifest) -> dict[str, object]:
    return {
        "format_version": config.SUPPORT_BUNDLE_FORMAT_VERSION,
        "bundle_id": str(manifest.bundle_id),
        "application_name": manifest.application_name,
        "application_version": manifest.application_version,
        "created_at_utc": _format_utc(manifest.created_at),
        "members": [
            {
                "name": member.name,
                "size_bytes": member.size,
                "sha256": member.sha256,
                "truncated": member.truncated,
            }
            for member in manifest.members
        ],
    }


def _json_bytes(payload: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SupportBundleCreationError(
            "Support diagnostics could not be serialized safely."
        ) from error


def _privacy_gate(
    members: tuple[DiagnosticMember, ...],
    policy: RedactionPolicy,
) -> None:
    for member in members:
        try:
            text = member.content.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise SupportBundleCreationError("Support diagnostics contain invalid text.") from error
        violation = policy.privacy_violation(text)
        if violation is not None:
            raise SupportBundleCreationError(
                f"Support bundle privacy validation failed ({violation})."
            )


def _write_archive(
    path: Path,
    members: tuple[DiagnosticMember, ...],
    created_at: datetime,
) -> None:
    total = sum(len(member.content) for member in members)
    if len(members) > config.SUPPORT_BUNDLE_MAX_MEMBER_COUNT:
        raise SupportBundleCreationError("Support bundle member limit was exceeded.")
    if total > config.SUPPORT_BUNDLE_MAX_UNCOMPRESSED_BYTES:
        raise SupportBundleCreationError("Support bundle content limit was exceeded.")
    with zipfile.ZipFile(
        path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for member in sorted(members, key=lambda candidate: candidate.name):
            info = zipfile.ZipInfo(
                filename=member.name,
                date_time=created_at.utctimetuple()[:6],
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, member.content)
    with path.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    if path.stat().st_size > config.SUPPORT_BUNDLE_MAX_ARCHIVE_BYTES:
        raise SupportBundleCreationError("Support bundle archive limit was exceeded.")


def _verify_archive(
    settings: Settings,
    policy: RedactionPolicy,
    path: Path,
    *,
    enforce_filename: bool,
) -> SupportBundleRecord:
    try:
        _require_archive(path, enforce_filename=enforce_filename)
        archive_size = path.stat().st_size
        if archive_size > config.SUPPORT_BUNDLE_MAX_ARCHIVE_BYTES:
            raise SupportBundleVerificationError("Support bundle archive limit was exceeded.")
        with zipfile.ZipFile(path, mode="r") as archive:
            infos = archive.infolist()
            _validate_member_shapes(infos)
            content = {info.filename: _read_bounded_member(archive, info) for info in infos}
        manifest = _parse_manifest(settings, policy, content[_MANIFEST_MEMBER])
        _verify_manifest_members(manifest, content)
        _verify_core_json(content)
        textual_members = tuple(
            DiagnosticMember(name=name, content=value) for name, value in content.items()
        )
        try:
            _privacy_gate(textual_members, policy)
        except SupportBundleCreationError as error:
            raise SupportBundleVerificationError(
                "Support bundle privacy validation failed."
            ) from error
        return SupportBundleRecord(
            path=path,
            filename=path.name,
            created_at=manifest.created_at,
            bundle_id=manifest.bundle_id,
            application_version=manifest.application_version,
            member_count=len(content),
            archive_size_bytes=archive_size,
            archive_sha256=_sha256_file(path),
        )
    except SupportBundleVerificationError:
        raise
    except (
        KeyError,
        OSError,
        UnicodeError,
        ValueError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as error:
        raise SupportBundleVerificationError("Support bundle verification failed.") from error
    except Exception as error:
        raise SupportBundleVerificationError("Support bundle verification failed.") from error


def _require_archive(path: Path, *, enforce_filename: bool) -> None:
    if enforce_filename and (
        path.suffix != config.SUPPORT_BUNDLE_EXTENSION
        or _BUNDLE_FILENAME_PATTERN.fullmatch(path.name) is None
    ):
        raise SupportBundleVerificationError("Support bundle filename is invalid.")
    if is_link_like(path) or not path.is_file():
        raise SupportBundleVerificationError("Support bundle must be an existing regular file.")


def _validate_member_shapes(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > config.SUPPORT_BUNDLE_MAX_MEMBER_COUNT:
        raise SupportBundleVerificationError("Support bundle has too many members.")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise SupportBundleVerificationError("Support bundle has duplicate members.")
    name_set = set(names)
    if not _CORE_MEMBERS.issubset(name_set):
        raise SupportBundleVerificationError("Support bundle core members are incomplete.")
    for info in infos:
        _validate_member_name(info.filename)
        mode = info.external_attr >> 16
        if info.is_dir() or (mode != 0 and not stat.S_ISREG(mode)):
            raise SupportBundleVerificationError("Support bundle contains a non-regular member.")
        if (
            info.filename not in _CORE_MEMBERS
            and _LOG_MEMBER_PATTERN.fullmatch(info.filename) is None
        ):
            raise SupportBundleVerificationError("Support bundle contains an unexpected member.")
        limit = (
            config.SUPPORT_BUNDLE_MAX_LOG_BYTES
            if info.filename.startswith("logs/")
            else config.SUPPORT_BUNDLE_MAX_JSON_BYTES
        )
        if info.file_size < 0 or info.file_size > limit:
            raise SupportBundleVerificationError("Support bundle member size is invalid.")
    total = sum(info.file_size for info in infos)
    if total > config.SUPPORT_BUNDLE_MAX_UNCOMPRESSED_BYTES:
        raise SupportBundleVerificationError("Support bundle content limit was exceeded.")


def _validate_member_name(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise SupportBundleVerificationError("Support bundle member path is unsafe.")


def _read_bounded_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> bytes:
    limit = (
        config.SUPPORT_BUNDLE_MAX_LOG_BYTES
        if info.filename.startswith("logs/")
        else config.SUPPORT_BUNDLE_MAX_JSON_BYTES
    )
    with archive.open(info, mode="r") as stream:
        content = stream.read(limit + 1)
    if len(content) != info.file_size or len(content) > limit:
        raise SupportBundleVerificationError("Support bundle member size is invalid.")
    return content


def _parse_manifest(
    settings: Settings,
    policy: RedactionPolicy,
    content: bytes,
) -> _SupportManifest:
    parsed = _parse_json_object(content)
    if set(parsed) != _MANIFEST_FIELDS:
        raise SupportBundleVerificationError("Support bundle manifest fields are invalid.")
    if _required_int(parsed, "format_version") != config.SUPPORT_BUNDLE_FORMAT_VERSION:
        raise SupportBundleVerificationError("Support bundle format version is not supported.")
    bundle_id = _required_uuid(parsed, "bundle_id")
    created_at = _required_utc(parsed, "created_at_utc")
    application_name = _required_string(parsed, "application_name")
    application_version = _required_string(parsed, "application_version")
    if not hmac.compare_digest(application_name, policy.redact(settings.app_name)):
        raise SupportBundleVerificationError("Support bundle application identity is invalid.")
    if not hmac.compare_digest(application_version, policy.redact(settings.app_version)):
        raise SupportBundleVerificationError("Support bundle application version is invalid.")
    raw_members = parsed.get("members")
    if not isinstance(raw_members, list):
        raise SupportBundleVerificationError("Support bundle manifest members are invalid.")
    members = tuple(_parse_manifest_member(value) for value in raw_members)
    if tuple(member.name for member in members) != tuple(sorted(member.name for member in members)):
        raise SupportBundleVerificationError("Support bundle manifest members are not ordered.")
    if len({member.name for member in members}) != len(members):
        raise SupportBundleVerificationError("Support bundle manifest has duplicate members.")
    return _SupportManifest(
        bundle_id=bundle_id,
        created_at=created_at,
        application_name=application_name,
        application_version=application_version,
        members=members,
    )


def _parse_manifest_member(value: JsonValue) -> _ManifestMember:
    if not isinstance(value, dict) or set(value) != _MEMBER_FIELDS:
        raise SupportBundleVerificationError("Support bundle manifest member fields are invalid.")
    name = _required_string(value, "name")
    _validate_member_name(name)
    size = _required_int(value, "size_bytes")
    if size < 0 or size > config.SUPPORT_BUNDLE_MAX_UNCOMPRESSED_BYTES:
        raise SupportBundleVerificationError("Support bundle manifest member size is invalid.")
    sha256 = _required_string(value, "sha256")
    if _SHA256_PATTERN.fullmatch(sha256) is None:
        raise SupportBundleVerificationError("Support bundle manifest member hash is invalid.")
    truncated = value.get("truncated")
    if not isinstance(truncated, bool):
        raise SupportBundleVerificationError("Support bundle manifest truncation flag is invalid.")
    if truncated and not name.startswith("logs/"):
        raise SupportBundleVerificationError("Only support bundle log members may be truncated.")
    return _ManifestMember(name, size, sha256, truncated)


def _verify_manifest_members(
    manifest: _SupportManifest,
    content: dict[str, bytes],
) -> None:
    declared_names = {member.name for member in manifest.members}
    actual_names = set(content) - {_MANIFEST_MEMBER}
    if declared_names != actual_names:
        raise SupportBundleVerificationError(
            "Support bundle manifest does not match archive members."
        )
    for member in manifest.members:
        actual = content[member.name]
        if len(actual) != member.size:
            raise SupportBundleVerificationError(
                "Support bundle member size does not match its manifest."
            )
        digest = hashlib.sha256(actual).hexdigest()
        if not hmac.compare_digest(digest, member.sha256):
            raise SupportBundleVerificationError(
                "Support bundle member hash does not match its manifest."
            )


def _verify_core_json(content: dict[str, bytes]) -> None:
    expected_fields = (
        (_APPLICATION_MEMBER, _APPLICATION_FIELDS),
        (_SYSTEM_MEMBER, _SYSTEM_FIELDS),
        (_DATABASE_MEMBER, _DATABASE_FIELDS),
        (_RUNTIME_MEMBER, _RUNTIME_FIELDS),
    )
    for name, fields in expected_fields:
        parsed = _parse_json_object(content[name])
        if set(parsed) != fields:
            raise SupportBundleVerificationError("Support bundle diagnostic fields are invalid.")
        if name == _APPLICATION_MEMBER:
            _verify_application_json(parsed)
        elif name == _SYSTEM_MEMBER:
            _verify_system_json(parsed)
        elif name == _DATABASE_MEMBER:
            _verify_database_json(parsed)
        else:
            _verify_runtime_json(parsed)


def _verify_application_json(parsed: dict[str, JsonValue]) -> None:
    string_fields = (
        "application_name",
        "application_version",
        "environment",
        "theme",
        "language",
        "log_level",
        "default_currency",
    )
    boolean_fields = ("debug", "auto_backup", "auto_snapshot")
    if not all(_safe_text(parsed.get(field)) for field in string_fields) or not all(
        isinstance(parsed.get(field), bool) for field in boolean_fields
    ):
        raise SupportBundleVerificationError("Support bundle application diagnostics are invalid.")


def _verify_system_json(parsed: dict[str, JsonValue]) -> None:
    string_fields = (
        "operating_system_family",
        "operating_system_release",
        "architecture",
        "python_implementation",
        "python_version",
        "locale",
        "timezone_offset",
    )
    packages = parsed.get("package_versions")
    expected_packages = {
        "PySide6",
        "SQLAlchemy",
        "Alembic",
        "Pydantic",
        "pydantic-settings",
        "Loguru",
        "platformdirs",
    }
    if (
        not all(_safe_text(parsed.get(field)) for field in string_fields)
        or not isinstance(parsed.get("packaged"), bool)
        or not isinstance(packages, dict)
        or set(packages) != expected_packages
        or not all(_safe_text(value) for value in packages.values())
    ):
        raise SupportBundleVerificationError("Support bundle system diagnostics are invalid.")


def _verify_database_json(parsed: dict[str, JsonValue]) -> None:
    optional_boolean_fields = (
        "schema_compatible",
        "sqlite_integrity",
        "foreign_key_enforcement",
    )
    optional_code_fields = (
        "current_alembic_revision",
        "expected_alembic_head",
        "journal_mode",
        "error_category",
    )
    filename = parsed.get("configured_database_filename")
    size = parsed.get("database_size_bytes")
    if (
        not _safe_code(parsed.get("backend_family"))
        or not isinstance(parsed.get("file_based"), bool)
        or (
            filename is not None
            and (
                not isinstance(filename, str)
                or not _safe_text(filename)
                or Path(filename).name != filename
                or "/" in filename
                or "\\" in filename
            )
        )
        or not isinstance(parsed.get("database_exists"), bool)
        or (size is not None and not _nonnegative_int(size))
        or not isinstance(parsed.get("health_check"), bool)
        or not isinstance(parsed.get("revision_current"), bool)
        or not all(
            parsed.get(field) is None or isinstance(parsed.get(field), bool)
            for field in optional_boolean_fields
        )
        or not all(
            parsed.get(field) is None or _safe_code(parsed.get(field))
            for field in optional_code_fields
        )
        or not _safe_code(parsed.get("status"))
    ):
        raise SupportBundleVerificationError("Support bundle database diagnostics are invalid.")


def _verify_runtime_json(parsed: dict[str, JsonValue]) -> None:
    directories = parsed.get("directories")
    expected_directories = {"data", "cache", "database", "export", "backup", "log"}
    count_fields = (
        "log_file_count",
        "log_omission_count",
        "backup_archive_count",
        "invalid_backup_count",
    )
    if (
        not isinstance(directories, dict)
        or set(directories) != expected_directories
        or not all(_directory_value(value) for value in directories.values())
        or not isinstance(parsed.get("active_log_exists"), bool)
        or not all(_nonnegative_int(parsed.get(field)) for field in count_fields)
        or parsed.get("pending_restore_state") not in {"none", "present", "corrupt"}
        or not isinstance(parsed.get("backup_scan_truncated"), bool)
    ):
        raise SupportBundleVerificationError("Support bundle runtime diagnostics are invalid.")


def _directory_value(value: JsonValue) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"exists", "writable"}
        and isinstance(value.get("exists"), bool)
        and isinstance(value.get("writable"), bool)
    )


def _safe_text(value: JsonValue) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 255
        and all(character.isprintable() for character in value)
    )


def _safe_code(value: JsonValue) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 255
        and _SAFE_CODE_PATTERN.fullmatch(value) is not None
    )


def _nonnegative_int(value: JsonValue) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _parse_json_object(content: bytes) -> dict[str, JsonValue]:
    try:
        decoded = content.decode("utf-8", errors="strict")
        parsed = cast(
            object,
            json.loads(
                decoded,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            ),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise SupportBundleVerificationError("Support bundle JSON is invalid.") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise SupportBundleVerificationError("Support bundle JSON must contain an object.")
    if _contains_float(parsed):
        raise SupportBundleVerificationError("Support bundle JSON contains an unsupported number.")
    return cast(dict[str, JsonValue], parsed)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Never:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _contains_float(value: object) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, list):
        return any(_contains_float(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_float(item) for item in value.values())
    return False


def _required_string(parsed: dict[str, JsonValue], key: str) -> str:
    value = parsed.get(key)
    if not isinstance(value, str) or not value:
        raise SupportBundleVerificationError("Support bundle manifest contains invalid text.")
    return value


def _required_int(parsed: dict[str, JsonValue], key: str) -> int:
    value = parsed.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SupportBundleVerificationError("Support bundle manifest contains an invalid number.")
    return value


def _required_uuid(parsed: dict[str, JsonValue], key: str) -> UUID:
    value = _required_string(parsed, key)
    try:
        parsed_uuid = UUID(value)
    except ValueError as error:
        raise SupportBundleVerificationError("Support bundle identifier is invalid.") from error
    if str(parsed_uuid) != value:
        raise SupportBundleVerificationError("Support bundle identifier is not canonical.")
    return parsed_uuid


def _required_utc(parsed: dict[str, JsonValue], key: str) -> datetime:
    value = _required_string(parsed, key)
    try:
        parsed_time = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise SupportBundleVerificationError("Support bundle timestamp is invalid.") from error
    if _format_utc(parsed_time) != value:
        raise SupportBundleVerificationError("Support bundle timestamp is not canonical UTC.")
    return parsed_time


def _ensure_support_directory(settings: Settings) -> Path:
    export_directory = Path(os.path.abspath(settings.export_directory))
    support_directory = export_directory / config.SUPPORT_DIRECTORY_NAME
    try:
        if is_link_like(export_directory) or not export_directory.is_dir():
            raise SupportBundleCreationError("The configured export directory is unavailable.")
        support_directory.mkdir(mode=0o700, parents=False, exist_ok=True)
        if is_link_like(support_directory) or not support_directory.is_dir():
            raise SupportBundleCreationError("The support bundle directory is invalid.")
    except SupportBundleCreationError:
        raise
    except OSError as error:
        raise SupportBundleCreationError(
            "The support bundle directory could not be created."
        ) from error
    return support_directory


def _bundle_path(directory: Path, created_at: datetime, bundle_id: UUID) -> Path:
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    filename = (
        f"mira-portfolio-support-{timestamp}-{bundle_id.hex[:12]}{config.SUPPORT_BUNDLE_EXTENSION}"
    )
    return directory / filename


def _reserve_temporary_archive(directory: Path) -> Path:
    try:
        with NamedTemporaryFile(
            mode="wb",
            prefix=".mira-support-",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as temporary:
            path = Path(temporary.name)
        return path
    except OSError as error:
        raise SupportBundleCreationError(
            "A temporary support bundle could not be created."
        ) from error


def _install_without_overwrite(source: Path, target: Path) -> None:
    descriptor: int | None = None
    reserved = False
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        descriptor = None
        reserved = True
        os.replace(source, target)
    except FileExistsError as error:
        raise SupportBundleCreationError(
            "A support bundle with the generated name already exists; retry."
        ) from error
    except OSError as error:
        raise SupportBundleCreationError(
            "The support bundle could not be installed atomically."
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if reserved and source.exists():
            _best_effort_unlink(target)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_HASH_CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_utc(value: datetime, *, creation: bool) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        error_type = SupportBundleCreationError if creation else SupportBundleVerificationError
        raise error_type("Support bundle timestamps must be timezone-aware UTC.")
    return value.astimezone(UTC).replace(microsecond=0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "SupportBundleDiagnosticsService",
    "verify_support_bundle",
]
