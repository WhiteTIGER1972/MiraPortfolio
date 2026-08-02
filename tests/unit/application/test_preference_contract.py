"""Tests for immutable application-facing preference contracts."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import cast

import pytest

from app.application.preferences import (
    LogLevelPreference,
    PreferenceField,
    PreferenceLoadStatus,
    PreferenceSaveResult,
    PreferenceSnapshot,
    ThemePreference,
    UserPreferences,
)


def preferences() -> UserPreferences:
    return UserPreferences(
        theme=ThemePreference.DARK,
        auto_backup=False,
        auto_snapshot=True,
        log_level=LogLevelPreference.WARNING,
    )


def test_user_preferences_are_immutable_and_strictly_typed() -> None:
    value = preferences()

    with pytest.raises(FrozenInstanceError):
        setattr(value, "log_level", LogLevelPreference.ERROR)
    with pytest.raises(TypeError, match="strict boolean"):
        UserPreferences(
            theme=ThemePreference.DARK,
            auto_backup=cast(bool, 1),
            auto_snapshot=True,
            log_level=LogLevelPreference.INFO,
        )


def test_log_level_preferences_cover_loguru_standard_levels() -> None:
    assert {level.value for level in LogLevelPreference} == {
        "TRACE",
        "DEBUG",
        "INFO",
        "SUCCESS",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }


def test_snapshot_is_immutable_and_uses_typed_field_sets() -> None:
    snapshot = PreferenceSnapshot(
        preferences=preferences(),
        status=PreferenceLoadStatus.LOADED,
        format_version=1,
        preference_file_exists=True,
        restart_required_fields=frozenset({PreferenceField.LOG_LEVEL}),
        overridden_by_environment=frozenset({PreferenceField.AUTO_BACKUP}),
        warning_category=None,
    )

    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "status", PreferenceLoadStatus.INVALID)
    assert snapshot.restart_required_fields == frozenset({PreferenceField.LOG_LEVEL})
    assert snapshot.overridden_by_environment == frozenset({PreferenceField.AUTO_BACKUP})


def test_save_result_requires_timezone_aware_utc_timestamp() -> None:
    saved_at = datetime(2026, 8, 2, 10, 30, tzinfo=UTC)
    result = PreferenceSaveResult(
        preferences=preferences(),
        saved_at_utc=saved_at,
        restart_required_fields=frozenset({PreferenceField.LOG_LEVEL}),
        overridden_by_environment=frozenset(),
    )

    assert result.saved_at_utc is saved_at
    assert result.saved_at_utc.utcoffset() == UTC.utcoffset(saved_at)
