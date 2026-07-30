"""Tests for the shared logging and diagnostics privacy policy."""

from pathlib import Path

import pytest

from app.core.redaction import RedactionPolicy
from app.core.settings import Settings


def policy_for(tmp_path: Path) -> RedactionPolicy:
    root = tmp_path / "private" / "alice"
    return RedactionPolicy.from_settings(
        Settings(
            _env_file=None,
            data_directory=root / "data",
            cache_directory=root / "cache",
            database_directory=root / "data" / "database",
            export_directory=root / "data" / "exports",
            backup_directory=root / "data" / "backups",
            log_directory=root / "logs",
            database_path=root / "data" / "database" / "portfolio.db",
            database_url="postgresql://private_user:private_password@localhost/mira",
        )
    )


def test_known_runtime_paths_and_database_url_are_redacted(tmp_path: Path) -> None:
    policy = policy_for(tmp_path)
    settings_path = tmp_path / "private" / "alice" / "data" / "database" / "portfolio.db"
    message = (
        f"opened {settings_path} using postgresql://private_user:private_password@localhost/mira"
    )

    redacted = policy.redact(message)

    assert "<DATABASE_PATH>" in redacted
    assert "<DATABASE_URL>" in redacted
    assert "private_password" not in redacted
    assert str(settings_path) not in redacted


@pytest.mark.parametrize(
    "message",
    (
        r"failed at C:\Users\alice\AppData\Local\Mira\file.txt",
        r"failed at \\server\users\alice\file.txt",
        "failed at /home/alice/.config/mira/file.txt",
        "failed at /Users/alice/Library/Mira/file.txt",
        "failed at /var/tmp/mira-private.txt",
    ),
)
def test_platform_user_paths_are_redacted(tmp_path: Path, message: str) -> None:
    assert "<PATH>" in policy_for(tmp_path).redact(message)


@pytest.mark.parametrize(
    "message",
    (
        "password=hunter2",
        "token: abcdef",
        "api_key = abcdef",
        "Authorization: Bearer-secret",
    ),
)
def test_sensitive_label_values_are_redacted(tmp_path: Path, message: str) -> None:
    redacted = policy_for(tmp_path).redact(message)

    assert "<REDACTED>" in redacted
    assert message.split()[-1] not in redacted


def test_safe_event_names_and_filenames_remain_readable(tmp_path: Path) -> None:
    message = "DATABASE_READY mira-portfolio.log backup-valid.mirabackup"

    assert policy_for(tmp_path).redact(message) == message


def test_identifiers_can_be_removed_from_persistent_logs(tmp_path: Path) -> None:
    message = "portfolio 12345678-1234-4234-8234-123456789abc failed"

    assert "<IDENTIFIER>" in policy_for(tmp_path).redact(message, identifiers=True)


def test_user_supplied_persisted_state_error_text_is_not_retained(tmp_path: Path) -> None:
    message = "Portfolio valuation rejected persisted state: Secret Asset Name"

    redacted = policy_for(tmp_path).redact(message)

    assert redacted.endswith("<ERROR>")
    assert "Secret Asset Name" not in redacted


def test_privacy_violation_reports_only_a_safe_category(tmp_path: Path) -> None:
    violation = policy_for(tmp_path).privacy_violation(r"unredacted C:\Users\alice\secret.txt")

    assert violation == "absolute_path"


def test_selected_sensitive_environment_value_is_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRA_TOKEN", "environment-secret-value")
    policy = policy_for(tmp_path)

    redacted = policy.redact("received environment-secret-value")

    assert "environment-secret-value" not in redacted
    assert "<REDACTED>" in redacted
