"""Strict v2, legacy v1, precedence, atomicity, and security preference tests."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.application.preferences import (
    LogLevelPreference,
    PreferenceField,
    PreferenceLoadStatus,
    PreferenceWarningCategory,
    UserPreferences,
)
from app.core import config, runtime_paths
from app.core.exceptions import PreferencePersistenceError, PreferenceSecurityError
from app.core.settings import Settings
from app.infrastructure.preferences import resolve_preferences

_PREFERENCE_ENVIRONMENT_NAMES = (
    "MIRA_THEME",
    "MIRA_AUTO_BACKUP",
    "MIRA_AUTO_SNAPSHOT",
    "MIRA_LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def isolate_preference_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _PREFERENCE_ENVIRONMENT_NAMES:
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


def encoded_document(version: int, values: dict[str, object]) -> bytes:
    return (
        json.dumps(
            {"format_version": version, "preferences": values},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def legacy_document(**values: object) -> bytes:
    return encoded_document(config.PREFERENCES_LEGACY_FORMAT_VERSION, values)


def document(**values: object) -> bytes:
    return encoded_document(config.PREFERENCES_FORMAT_VERSION, values)


def proposed_preferences(
    log_level: LogLevelPreference = LogLevelPreference.WARNING,
) -> UserPreferences:
    return UserPreferences(log_level=log_level)


def temporary_artifacts(target: Path) -> list[Path]:
    return list(target.parent.glob(".preferences-*.tmp"))


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
    assert not preference_path(settings).parent.exists()


def test_version_two_log_level_loads_and_overrides_default(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, document(log_level="WARNING"))

    resolution = resolve_preferences(settings)
    snapshot = resolution.service.get_current()

    assert resolution.settings.log_level == "WARNING"
    assert resolution.settings.theme == settings.theme
    assert resolution.settings.auto_backup is settings.auto_backup
    assert resolution.settings.auto_snapshot is settings.auto_snapshot
    assert snapshot.status is PreferenceLoadStatus.LOADED
    assert snapshot.format_version == 2
    assert snapshot.preferences == proposed_preferences()


def test_empty_version_two_preferences_preserve_base_log_level(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, document())

    snapshot = resolve_preferences(settings).service.get_current()

    assert snapshot.status is PreferenceLoadStatus.LOADED
    assert snapshot.format_version == 2
    assert snapshot.preferences.log_level is LogLevelPreference.INFO


def test_unknown_top_level_fields_are_rejected(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    original = b'{"extra":true,"format_version":2,"preferences":{}}\n'
    target = write_raw(settings, original)

    snapshot = resolve_preferences(settings).service.get_current()

    assert snapshot.status is PreferenceLoadStatus.INVALID
    assert target.read_bytes() == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("theme", "dark"),
        ("auto_backup", False),
        ("auto_snapshot", False),
        ("database_url", "sqlite:///private.db"),
        ("path", "C:/private/preferences.json"),
        ("source", "https://private.invalid"),
    ],
)
def test_version_two_rejects_removed_unknown_path_and_url_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, encoded_document(config.PREFERENCES_FORMAT_VERSION, {field: value}))

    snapshot = resolve_preferences(settings).service.get_current()

    assert snapshot.status is PreferenceLoadStatus.INVALID
    assert snapshot.warning_category is PreferenceWarningCategory.INVALID_FORMAT


@pytest.mark.parametrize(
    "content",
    [
        b'{"format_version":2,"format_version":2,"preferences":{}}',
        b'{"format_version":2,"preferences":{"log_level":"INFO","log_level":"ERROR"}}',
    ],
)
def test_duplicate_json_keys_are_rejected(tmp_path: Path, content: bytes) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, content)

    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_constants_are_rejected(tmp_path: Path, constant: str) -> None:
    settings = preference_settings(tmp_path)
    write_raw(
        settings,
        f'{{"format_version":2,"preferences":{{"log_level":{constant}}}}}'.encode(),
    )

    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )


def test_oversized_file_is_rejected_before_unbounded_read_and_preserved(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    original = b"{" + b"x" * config.PREFERENCES_MAX_FILE_BYTES
    target = write_raw(settings, original)

    snapshot = resolve_preferences(settings).service.get_current()

    assert snapshot.status is PreferenceLoadStatus.INVALID
    assert target.read_bytes() == original


@pytest.mark.parametrize("value", ["VERBOSE", True, None, 7])
def test_invalid_version_two_log_levels_are_rejected(tmp_path: Path, value: object) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, document(log_level=value))

    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )


def test_invalid_version_type_and_unsupported_versions_are_distinguished(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, b'{"format_version":true,"preferences":{}}')
    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )

    write_raw(settings, encoded_document(3, {"log_level": "DEBUG"}))
    snapshot = resolve_preferences(settings).service.get_current()
    assert snapshot.status is PreferenceLoadStatus.UNSUPPORTED_VERSION
    assert snapshot.format_version == 3
    assert snapshot.warning_category is PreferenceWarningCategory.UNSUPPORTED_VERSION


def test_comments_and_invalid_files_remain_byte_for_byte_unchanged(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    original = b'{"format_version":2,"preferences":{}} // private comment\n'
    target = write_raw(settings, original)

    resolution = resolve_preferences(settings)

    assert resolution.settings.model_dump() == settings.model_dump()
    assert resolution.service.get_current().status is PreferenceLoadStatus.INVALID
    assert target.read_bytes() == original


def test_historical_version_one_file_loads_only_its_log_level(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path).model_copy(update={"theme": "base-theme"})
    original = legacy_document(
        theme="dark",
        auto_backup=False,
        auto_snapshot=False,
        log_level="WARNING",
    )
    target = write_raw(settings, original)

    resolution = resolve_preferences(settings)
    snapshot = resolution.service.get_current()

    assert snapshot.status is PreferenceLoadStatus.LOADED
    assert snapshot.format_version == 1
    assert snapshot.preferences == proposed_preferences()
    assert resolution.settings.log_level == "WARNING"
    assert resolution.settings.theme == "base-theme"
    assert resolution.settings.auto_backup is True
    assert resolution.settings.auto_snapshot is True
    assert target.read_bytes() == original


def test_reload_of_version_one_does_not_rewrite_or_mutate_running_settings(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    original = legacy_document(theme="dark", auto_backup=False, log_level="ERROR")
    target = write_raw(settings, original)
    resolution = resolve_preferences(settings)
    running = resolution.settings

    snapshot = resolution.service.reload()

    assert snapshot.status is PreferenceLoadStatus.LOADED
    assert snapshot.format_version == 1
    assert snapshot.restart_required_fields == frozenset()
    assert resolution.settings is running
    assert target.read_bytes() == original


def test_version_one_unknown_fields_are_rejected(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, legacy_document(language="en", log_level="INFO"))

    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )


@pytest.mark.parametrize("field", ["auto_backup", "auto_snapshot"])
@pytest.mark.parametrize("value", [1, "false", None])
def test_version_one_malformed_booleans_are_rejected(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, legacy_document(**{field: value}))

    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )


@pytest.mark.parametrize("theme", ["light", "DARK", None, 1])
def test_version_one_unsupported_historical_theme_is_rejected(
    tmp_path: Path,
    theme: object,
) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, legacy_document(theme=theme))

    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )


def test_version_one_theme_validation_is_pinned_to_historical_dark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, legacy_document(theme="dark", log_level="INFO"))
    monkeypatch.setattr(config, "THEME", "future-theme")

    snapshot = resolve_preferences(settings).service.get_current()

    assert snapshot.status is PreferenceLoadStatus.LOADED
    assert snapshot.format_version == 1


@pytest.mark.parametrize("log_level", ["VERBOSE", True, None])
def test_version_one_invalid_log_levels_are_rejected(
    tmp_path: Path,
    log_level: object,
) -> None:
    settings = preference_settings(tmp_path)
    write_raw(settings, legacy_document(log_level=log_level))

    assert (
        resolve_preferences(settings).service.get_current().status is PreferenceLoadStatus.INVALID
    )


def test_saving_after_version_one_load_writes_canonical_version_two(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    write_raw(
        settings,
        legacy_document(
            theme="dark",
            auto_backup=False,
            auto_snapshot=False,
            log_level="DEBUG",
        ),
    )
    service = resolve_preferences(settings).service

    service.save(proposed_preferences(LogLevelPreference.WARNING))

    assert preference_path(settings).read_bytes() == (
        b'{"format_version":2,"preferences":{"log_level":"WARNING"}}\n'
    )


def test_environment_override_wins_over_version_two_even_at_default_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRA_LOG_LEVEL", "INFO")
    settings = preference_settings(tmp_path)
    write_raw(settings, document(log_level="DEBUG"))

    resolution = resolve_preferences(settings)
    snapshot = resolution.service.get_current()

    assert resolution.settings.log_level == "INFO"
    assert snapshot.overridden_by_environment == frozenset({PreferenceField.LOG_LEVEL})


def test_environment_override_wins_over_version_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRA_LOG_LEVEL", "ERROR")
    settings = preference_settings(tmp_path)
    write_raw(settings, legacy_document(theme="dark", log_level="DEBUG"))

    resolution = resolve_preferences(settings)

    assert resolution.settings.log_level == "ERROR"
    assert resolution.service.get_current().format_version == 1
    assert resolution.service.get_current().overridden_by_environment == frozenset(
        {PreferenceField.LOG_LEVEL}
    )


def test_dotenv_log_level_override_wins_and_removed_fields_remain_settings_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MIRA_THEME=dark\nMIRA_AUTO_BACKUP=false\nMIRA_AUTO_SNAPSHOT=false\nMIRA_LOG_LEVEL=ERROR\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = preference_settings(tmp_path)
    write_raw(settings, document(log_level="DEBUG"))

    resolution = resolve_preferences(settings)
    snapshot = resolution.service.get_current()

    assert resolution.settings.theme == "dark"
    assert resolution.settings.auto_backup is False
    assert resolution.settings.auto_snapshot is False
    assert resolution.settings.log_level == "ERROR"
    assert snapshot.overridden_by_environment == frozenset({PreferenceField.LOG_LEVEL})


def test_removed_environment_fields_affect_settings_not_preference_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRA_THEME", "dark")
    monkeypatch.setenv("MIRA_AUTO_BACKUP", "false")
    monkeypatch.setenv("MIRA_AUTO_SNAPSHOT", "false")
    settings = preference_settings(tmp_path)
    write_raw(settings, document(log_level="WARNING"))

    resolution = resolve_preferences(settings)
    snapshot = resolution.service.get_current()

    assert resolution.settings.theme == "dark"
    assert resolution.settings.auto_backup is False
    assert resolution.settings.auto_snapshot is False
    assert resolution.settings.log_level == "WARNING"
    assert snapshot.overridden_by_environment == frozenset()
    assert snapshot.preferences == proposed_preferences()


def test_unrelated_environment_value_is_not_exposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRA_TOKEN", "unrelated-environment-secret")
    settings = preference_settings(tmp_path)

    snapshot = resolve_preferences(settings).service.get_current()

    assert "unrelated-environment-secret" not in repr(snapshot)
    assert snapshot.overridden_by_environment == frozenset()


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
    write_raw(settings, document(log_level="ERROR"))

    loaded = resolve_preferences(settings).settings

    assert "database_url" not in loaded.model_fields_set
    assert "database_path" not in loaded.model_fields_set
    assert PreferenceField.LOG_LEVEL.value in loaded.model_fields_set


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
    assert first == b'{"format_version":2,"preferences":{"log_level":"WARNING"}}\n'
    assert b"theme" not in first
    assert b"auto_backup" not in first
    assert b"auto_snapshot" not in first
    assert preference_path(settings).parent.is_dir()
    assert temporary_artifacts(preference_path(settings)) == []


def test_environment_owned_save_writes_empty_version_two_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRA_LOG_LEVEL", "ERROR")
    settings = preference_settings(tmp_path)
    settings.data_directory.mkdir()
    resolution = resolve_preferences(settings)

    result = resolution.service.save(proposed_preferences(LogLevelPreference.DEBUG))

    assert preference_path(settings).read_bytes() == b'{"format_version":2,"preferences":{}}\n'
    assert result.preferences.log_level is LogLevelPreference.ERROR
    assert result.overridden_by_environment == frozenset({PreferenceField.LOG_LEVEL})
    assert result.restart_required_fields == frozenset()
    assert resolution.settings.log_level == "ERROR"


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


def test_file_fsync_failure_leaves_existing_file_unchanged_and_cleans_temps(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    original = legacy_document(theme="dark", log_level="INFO")
    target = write_raw(settings, original)

    def fail_sync(_: int) -> None:
        raise OSError("private fsync failure")

    service = resolve_preferences(settings, file_sync=fail_sync).service

    with pytest.raises(PreferencePersistenceError):
        service.save(proposed_preferences())

    assert target.read_bytes() == original
    assert temporary_artifacts(target) == []


def test_replace_failure_leaves_existing_file_unchanged_and_cleans_temps(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    original = document(log_level="INFO")
    target = write_raw(settings, original)

    def fail_replace(_: Path, __: Path) -> None:
        raise OSError("private replace failure")

    service = resolve_preferences(settings, replace=fail_replace).service

    with pytest.raises(PreferencePersistenceError):
        service.save(proposed_preferences())

    assert target.read_bytes() == original
    assert temporary_artifacts(target) == []


def test_final_verification_failure_rolls_back_existing_file_and_cleans_temps(
    tmp_path: Path,
) -> None:
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
    assert temporary_artifacts(target) == []


def test_symlink_reparse_and_nonregular_final_targets_are_rejected(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    target = preference_path(settings)
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(document(log_level="INFO"))
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks and reparse points are unavailable in this environment.")

    resolution = resolve_preferences(settings)
    assert resolution.service.get_current().warning_category is PreferenceWarningCategory.SECURITY
    with pytest.raises(PreferenceSecurityError):
        resolution.service.save(proposed_preferences())
    with pytest.raises(PreferenceSecurityError):
        resolution.service.reset()

    target.unlink()
    target.mkdir()
    resolution = resolve_preferences(settings)
    with pytest.raises(PreferenceSecurityError):
        resolution.service.save(proposed_preferences())
    with pytest.raises(PreferenceSecurityError):
        resolution.service.reset()


def test_hardlinked_final_target_is_rejected_for_save_and_reset(tmp_path: Path) -> None:
    settings = preference_settings(tmp_path)
    target = preference_path(settings)
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(document(log_level="INFO"))
    try:
        os.link(outside, target)
    except OSError:
        pytest.skip("Hardlinks are unavailable in this environment.")

    resolution = resolve_preferences(settings)
    assert resolution.service.get_current().warning_category is PreferenceWarningCategory.SECURITY
    with pytest.raises(PreferenceSecurityError):
        resolution.service.save(proposed_preferences())
    with pytest.raises(PreferenceSecurityError):
        resolution.service.reset()
    assert outside.read_bytes() == document(log_level="INFO")


def test_preference_path_is_contained_and_unrelated_files_survive(tmp_path: Path) -> None:
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


def test_reset_deletes_only_preferences_file_is_idempotent_and_uses_base(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    target = write_raw(settings, document(log_level="WARNING"))
    unrelated = target.parent / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    service = resolve_preferences(settings).service

    first = service.reset()
    second = service.reset()

    assert first.status is PreferenceLoadStatus.MISSING
    assert second.status is PreferenceLoadStatus.MISSING
    assert first.preferences.log_level is LogLevelPreference.INFO
    assert not target.exists()
    assert target.parent.is_dir()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_reset_returns_environment_owned_log_level_without_mutating_running_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRA_LOG_LEVEL", "ERROR")
    settings = preference_settings(tmp_path)
    target = write_raw(settings, document(log_level="DEBUG"))
    resolution = resolve_preferences(settings)
    running = resolution.settings

    snapshot = resolution.service.reset()

    assert snapshot.preferences.log_level is LogLevelPreference.ERROR
    assert snapshot.overridden_by_environment == frozenset({PreferenceField.LOG_LEVEL})
    assert snapshot.restart_required_fields == frozenset()
    assert resolution.settings is running
    assert running.log_level == "ERROR"
    assert not target.exists()


def test_save_does_not_mutate_running_settings_and_only_log_level_restarts(
    tmp_path: Path,
) -> None:
    settings = preference_settings(tmp_path)
    settings.data_directory.mkdir()
    resolution = resolve_preferences(settings)
    running = resolution.settings

    result = resolution.service.save(proposed_preferences(LogLevelPreference.ERROR))

    assert result.restart_required_fields == frozenset({PreferenceField.LOG_LEVEL})
    assert result.overridden_by_environment == frozenset()
    assert resolution.settings is running
    assert running.log_level == "INFO"
    assert running.theme == settings.theme
    assert running.auto_backup is settings.auto_backup
    assert running.auto_snapshot is settings.auto_snapshot
