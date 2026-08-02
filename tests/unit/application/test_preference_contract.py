"""Tests for the log-level-only application preference contract."""

import inspect
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from typing import cast

import pytest

from app.application import preferences as preference_contract
from app.application.preferences import (
    LogLevelPreference,
    PreferenceField,
    PreferenceLoadStatus,
    PreferenceSaveResult,
    PreferenceSnapshot,
    UserPreferences,
)


def preferences() -> UserPreferences:
    return UserPreferences(log_level=LogLevelPreference.WARNING)


def test_public_preference_scope_contains_only_log_level() -> None:
    assert tuple(PreferenceField) == (PreferenceField.LOG_LEVEL,)
    assert tuple(field.name for field in fields(UserPreferences)) == ("log_level",)
    assert not hasattr(preference_contract, "ThemePreference")
    assert "ThemePreference" not in preference_contract.__all__


@pytest.mark.parametrize("removed_field", ["theme", "auto_backup", "auto_snapshot"])
def test_removed_fields_cannot_be_passed_to_user_preferences(removed_field: str) -> None:
    signature = inspect.signature(UserPreferences)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        signature.bind(
            log_level=LogLevelPreference.INFO,
            **{removed_field: object()},
        )


def test_user_preferences_are_immutable_and_strictly_typed() -> None:
    value = preferences()

    with pytest.raises(FrozenInstanceError):
        setattr(value, "log_level", LogLevelPreference.ERROR)
    with pytest.raises(TypeError, match="supported LogLevelPreference"):
        UserPreferences(log_level=cast(LogLevelPreference, "INFO"))


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


def test_snapshot_is_immutable_and_uses_log_level_only_field_sets() -> None:
    snapshot = PreferenceSnapshot(
        preferences=preferences(),
        status=PreferenceLoadStatus.LOADED,
        format_version=2,
        preference_file_exists=True,
        restart_required_fields=frozenset({PreferenceField.LOG_LEVEL}),
        overridden_by_environment=frozenset({PreferenceField.LOG_LEVEL}),
        warning_category=None,
    )

    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "status", PreferenceLoadStatus.INVALID)
    assert snapshot.restart_required_fields == frozenset({PreferenceField.LOG_LEVEL})
    assert snapshot.overridden_by_environment == frozenset({PreferenceField.LOG_LEVEL})


def test_save_result_uses_an_aware_utc_timestamp() -> None:
    saved_at = datetime(2026, 8, 2, 10, 30, tzinfo=UTC)
    result = PreferenceSaveResult(
        preferences=preferences(),
        saved_at_utc=saved_at,
        restart_required_fields=frozenset({PreferenceField.LOG_LEVEL}),
        overridden_by_environment=frozenset(),
    )

    assert result.saved_at_utc is saved_at
    assert result.saved_at_utc.utcoffset() == UTC.utcoffset(saved_at)
