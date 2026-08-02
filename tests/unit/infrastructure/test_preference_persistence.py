"""Strict format, precedence, atomicity, and filesystem-security preference tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.application.preferences import (
    LogLevelPreference,
    PreferenceField,
    PreferenceLoadStatus,
    PreferenceWarningCategory,
    ThemePreference,
    UserPreferences,
)
from app.core import config, runtime_paths
from app.core.exceptions import PreferencePersistenceError, PreferenceSecurityError
from app.core.settings import Settings
from app.infrastructure.preferences import resolve_preferences

_APPROVED_ENVIRONMENT_NAMES = (
    "MIRA_THEME",
    "MIRA_AUTO_BACKUP",
    "MIRA_AUTO_SNAPSHOT",
    "MIRA_LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def isolate_approved_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _APPROVED_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield


def preference_settings(tmp_path: Path) -> Settings:
    root = tmp_path / "runtime"
    database = root / "database" / "portfolio.db"
    return Settings(
        data_directory=root,
        cache_directory=root / "cache",
        database_directory=database.parent,
        export_directory=root / "exports",
        backup_directory=root / "backups",
        log_directory=root / "logs",
        database_path=database,
        database_url=f"sqlite:///{database.as_posix()}",
    )


def preference_path(settings: Settings) -> Path:
    return runtime_paths.preferences_file(settings.data_directory)


def write_raw(settings: Settings, content: bytes) -> Path:
    target = preference_path(settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def document(**preferences: object) -> bytes:
    return (
        json.dumps(
            {
                "format_version": config.PREFERENCES_FORMAT_VERSION,
                "preferences": preferences,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def proposed_preferences(
    *,
    auto_backup: bool = False,
    auto_snapshot: bool = True,
    log_level: LogLevelPreference = LogLevelPreference.WARNING,
) -> UserPreferences:
    return UserPreferences(
        theme=ThemePreference.DARK,
        auto_backup=auto_backup,
        auto_snapshot=auto_snapshot,
        log_level=log_level,
    )


def test_missing_directory_returns_missing_without_creating_anything(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)

    resolution = resolve_preferences(settings)

    assert resolution.service.get_current().status is PreferenceLoadStatus.MISSING
    assert resolution.settings is not settings
    assert not settings.data_directory.exists()


def test_missing_file_returns_missing_without_creating_settings_directory(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    settings.data_directory.mkdir()

    snapshot = resolve_preferences(settings).service.get_current()

    assert snapshot.status is PreferenceLoadStatus.MISSING
    assert not runtime_paths.preferences_directory(settings.data_directory).exists()


def test_valid_version_one_file_loads_and_overrides_defaults(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    write_raw(
        settings,
        document(auto_backup=False, log_level="WARNING", theme="dark"),
    )

    resolution = resolve_preferences(settings)
    snapshot = resolution.service.get_current()

    assert snapshot.status is PreferenceLoadStatus.LOADED
    assert snapshot.format_version == 1
    assert snapshot.preference_file_exists
    assert resolution.settings.auto_backup is False
    assert resolution.settings.log_level == "WARNING"
    assert resolution.settings.database_url == settings.database_url
    assert resolution.settings.database_path == settings.database_path


@pytest.mark.parametrize(
    "content",
    (
        b'{"format_version":1,"format_version":1,"preferences":{}}',
        b'{"format_version":1,"preferences":{"log_level":"INFO","log_level":"ERROR"}}',
    ),
)
def test_duplicate_json_keys_are_rejected(tmp_path: Path, content: bytes) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, content)

    snapshot = resolve_preferences(settings).service.get_current()

    assert snapshot.status is PreferenceLoadStatus.INVALID
    assert snapshot.warning_category is PreferenceWarningCategory.INVALID_FORMAT


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_nonfinite_json_constants_are_rejected(tmp_path: Path, constant: str) -> None:
    settings = preference_settings(tmp_path)
    write_raw(
        settings,
        f'{{"format_version":1,"preferences":{{"auto_backup":{constant}}}}}'.encode(),
    )

    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )


def test_unknown_top_level_and_preference_fields_are_rejected(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    target = write_raw(
        settings,
        b'{"format_version":1,"preferences":{},"metadata":"forbidden"}',
    )
    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )

    target.write_bytes(document(unknown_field="value"))
    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )


def test_unsupported_format_version_is_reported_without_parsing_values(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    write_raw(
        settings,
        b'{"format_version":2,"preferences":{"future_field":{"arbitrary":true}}}',
    )

    snapshot = resolve_preferences(settings).service.get_current()

    assert snapshot.status is PreferenceLoadStatus.UNSUPPORTED_VERSION
    assert snapshot.format_version == 2
    assert snapshot.warning_category is PreferenceWarningCategory.UNSUPPORTED_VERSION


def test_oversized_file_is_rejected_and_preserved(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    original = b"{" + (b"x" * config.PREFERENCES_MAX_FILE_BYTES) + b"}"
    target = write_raw(settings, original)

    snapshot = resolve_preferences(settings).service.get_current()

    assert snapshot.status is PreferenceLoadStatus.INVALID
    assert target.read_bytes() == original


@pytest.mark.parametrize(
    "preferences",
    (
        {"auto_backup": 1},
        {"auto_snapshot": "false"},
        {"theme": "light"},
        {"log_level": "VERBOSE"},
        {"update_interval": 0},
        {"update_interval": 999999999},
        {"database_path": "../private.db"},
        {"database_url": "postgresql://user:secret@example.test/mira"},
    ),
)
def test_invalid_types_enums_excluded_ranges_paths_and_urls_are_rejected(
    tmp_path: Path,
    preferences: dict[str, object],
) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, document(**preferences))

    snapshot = resolve_preferences(settings).service.get_current()

    assert snapshot.status is PreferenceLoadStatus.INVALID
    assert snapshot.preferences.log_level is LogLevelPreference.INFO
    assert resolution_sensitive_text(snapshot) == ""


def resolution_sensitive_text(snapshot: object) -> str:
    rendered = repr(snapshot)
    forbidden = ("postgresql://", "private.db", "secret@example")
    return "".join(value for value in forbidden if value in rendered)


def test_invalid_file_remains_byte_for_byte_unchanged(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    original = b'{"format_version":1,"preferences":{"log_level":"SECRET"}}\n'
    target = write_raw(settings, original)

    resolve_preferences(settings)

    assert target.read_bytes() == original


def test_environment_override_wins_even_when_equal_to_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRA_LOG_LEVEL", "INFO")
    settings = preference_settings(tmp_path)
    write_raw(settings, document(log_level="DEBUG"))

    resolution = resolve_preferences(settings)
    snapshot = resolution.service.get_current()

    assert resolution.settings.log_level == "INFO"
    assert PreferenceField.LOG_LEVEL in snapshot.overridden_by_environment


def test_dotenv_override_wins_over_persisted_preference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MIRA_AUTO_BACKUP=false\nMIRA_LOG_LEVEL=ERROR\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    settings = preference_settings(tmp_path)
    write_raw(settings, document(auto_backup=True, log_level="DEBUG"))

    resolution = resolve_preferences(settings)
    snapshot = resolution.service.get_current()

    assert resolution.settings.auto_backup is False
    assert resolution.settings.log_level == "ERROR"
    assert snapshot.overridden_by_environment == frozenset(
        {PreferenceField.AUTO_BACKUP, PreferenceField.LOG_LEVEL}
    )


def test_unrelated_environment_value_is_not_exposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRA_TOKEN", "unrelated-environment-secret")
    settings = preference_settings(tmp_path)

    snapshot = resolve_preferences(settings).service.get_current()

    assert "unrelated-environment-secret" not in repr(snapshot)
    assert snapshot.overridden_by_environment == frozenset()


def test_invalid_and_missing_preferences_preserve_base_settings(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    missing = resolve_preferences(settings)
    assert missing.settings.model_dump() == settings.model_dump()

    write_raw(settings, b"not-json")
    invalid = resolve_preferences(settings)
    assert invalid.settings.model_dump() == settings.model_dump()
    assert invalid.service.get_current().status is PreferenceLoadStatus.INVALID


def test_resolution_preserves_unrelated_settings_source_metadata(tmp_path: Path) -> None:
    root = tmp_path / "metadata-runtime"
    settings = Settings(
        data_directory=root,
        cache_directory=root / "cache",
        database_directory=root / "database",
        export_directory=root / "exports",
        backup_directory=root / "backups",
        log_directory=root / "logs",
    )
    assert "database_url" not in settings.model_fields_set

    missing = resolve_preferences(settings).settings
    write_raw(settings, document(auto_backup=False))
    loaded = resolve_preferences(settings).settings

    assert missing is not settings
    assert missing.model_fields_set == settings.model_fields_set
    assert "database_url" not in loaded.model_fields_set
    assert "database_path" not in loaded.model_fields_set
    assert PreferenceField.AUTO_BACKUP.value in loaded.model_fields_set


def test_explicit_save_creates_only_dedicated_directory_and_is_deterministic(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    settings.data_directory.mkdir()
    service = resolve_preferences(settings).service
    proposed = proposed_preferences()

    service.save(proposed)
    first = preference_path(settings).read_bytes()
    service.save(proposed)
    second = preference_path(settings).read_bytes()

    assert first == second
    assert first == (
        b'{"format_version":1,"preferences":{"auto_backup":false,'
        b'"auto_snapshot":true,"log_level":"WARNING","theme":"dark"}}\n'
    )
    assert preference_path(settings).parent.is_dir()
    assert list(preference_path(settings).parent.glob("*.tmp")) == []


def test_save_flushes_file_and_attempts_directory_durability(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    settings.data_directory.mkdir()
    file_syncs: list[int] = []
    directory_syncs: list[Path] = []
    resolution = resolve_preferences(
        settings,
        file_sync=file_syncs.append,
        directory_sync=directory_syncs.append,
    )

    resolution.service.save(proposed_preferences())

    assert file_syncs
    assert directory_syncs == [runtime_paths.preferences_directory(settings.data_directory)]


def test_file_fsync_failure_leaves_existing_file_unchanged(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    original = document(log_level="INFO")
    target = write_raw(settings, original)

    def fail_sync(_: int) -> None:
        raise OSError("private fsync failure")

    service = resolve_preferences(settings, file_sync=fail_sync).service

    with pytest.raises(PreferencePersistenceError):
        service.save(proposed_preferences())

    assert target.read_bytes() == original
    assert list(target.parent.glob(".preferences-*.tmp")) == []


def test_replace_failure_leaves_existing_file_unchanged(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    original = document(log_level="INFO")
    target = write_raw(settings, original)

    def fail_replace(_: Path, __: Path) -> None:
        raise OSError("private replace failure")

    service = resolve_preferences(settings, replace=fail_replace).service

    with pytest.raises(PreferencePersistenceError):
        service.save(proposed_preferences())

    assert target.read_bytes() == original
    assert list(target.parent.glob(".preferences-*.tmp")) == []


def test_final_verification_failure_rolls_back_existing_file(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    original = document(log_level="INFO")
    target = write_raw(settings, original)

    def fail_verification(_: Path) -> None:
        raise RuntimeError("private verification failure")

    service = resolve_preferences(
        settings,
        post_replace_verify_hook=fail_verification,
    ).service

    with pytest.raises(PreferencePersistenceError):
        service.save(proposed_preferences())

    assert target.read_bytes() == original
    assert list(target.parent.glob(".preferences-*.tmp")) == []


def test_symlinked_or_nonregular_final_target_is_rejected(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    target = preference_path(settings)
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(document(log_level="INFO"))
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks are unavailable in this test environment.")

    resolution = resolve_preferences(settings)
    assert resolution.service.get_current().warning_category is PreferenceWarningCategory.SECURITY
    with pytest.raises(PreferenceSecurityError):
        resolution.service.save(proposed_preferences())

    target.unlink()
    target.mkdir()
    resolution = resolve_preferences(settings)
    assert resolution.service.get_current().warning_category is PreferenceWarningCategory.SECURITY
    with pytest.raises(PreferenceSecurityError):
        resolution.service.save(proposed_preferences())


def test_preference_path_is_strictly_contained_and_unrelated_files_survive(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    settings.data_directory.mkdir()
    unrelated = settings.data_directory / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    service = resolve_preferences(settings).service

    service.save(proposed_preferences())

    target = preference_path(settings)
    assert target.parent.parent == settings.data_directory
    assert target.name == config.PREFERENCES_FILENAME
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_reset_deletes_only_preferences_file_and_is_idempotent(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    target = write_raw(settings, document(log_level="WARNING"))
    unrelated = target.parent / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    service = resolve_preferences(settings).service

    first = service.reset()
    second = service.reset()

    assert first.status is PreferenceLoadStatus.MISSING
    assert second.status is PreferenceLoadStatus.MISSING
    assert not target.exists()
    assert target.parent.is_dir()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_reset_rejects_symlink_without_deleting_target(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    target = preference_path(settings)
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(document(log_level="INFO"))
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks are unavailable in this test environment.")
    service = resolve_preferences(settings).service

    with pytest.raises(PreferenceSecurityError):
        service.reset()

    assert outside.read_bytes() == document(log_level="INFO")


def test_save_omits_environment_overridden_value_and_does_not_mutate_running_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRA_LOG_LEVEL", "ERROR")
    settings = preference_settings(tmp_path)
    settings.data_directory.mkdir()
    resolution = resolve_preferences(settings)
    running = resolution.settings

    result = resolution.service.save(proposed_preferences(log_level=LogLevelPreference.DEBUG))
    persisted = preference_path(settings).read_text(encoding="utf-8")

    assert result.preferences.log_level is LogLevelPreference.ERROR
    assert result.overridden_by_environment == frozenset({PreferenceField.LOG_LEVEL})
    assert '"log_level"' not in persisted
    assert running.log_level == "ERROR"
    assert getattr(resolution.service, "_settings") is running


def test_restart_required_fields_match_effective_changes(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    settings.data_directory.mkdir()
    resolution = resolve_preferences(settings)

    result = resolution.service.save(
        proposed_preferences(
            auto_backup=False,
            auto_snapshot=False,
            log_level=LogLevelPreference.ERROR,
        )
    )

    assert result.restart_required_fields == frozenset(
        {
            PreferenceField.AUTO_BACKUP,
            PreferenceField.AUTO_SNAPSHOT,
            PreferenceField.LOG_LEVEL,
        }
    )
    assert resolution.settings.auto_backup is True
    assert resolution.settings.auto_snapshot is True
    assert resolution.settings.log_level == "INFO"
