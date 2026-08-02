"""Tests for synchronous privacy-safe persistent logging."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from loguru import logger

from app.core import config
from app.core.exceptions import ConfigurationError
from app.core.logging import configure_logging
from app.core.settings import Settings


@pytest.fixture(autouse=True)
def close_loguru_sinks() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


def logging_settings(tmp_path: Path) -> Settings:
    root = tmp_path / "runtime"
    logs = root / "logs"
    logs.mkdir(parents=True)
    return Settings(
        _env_file=None,
        data_directory=root / "data",
        cache_directory=root / "cache",
        database_directory=root / "data" / "database",
        export_directory=root / "data" / "exports",
        backup_directory=root / "data" / "backups",
        log_directory=logs,
        database_path=root / "data" / "database" / "portfolio.db",
        database_url=("postgresql://diagnostic_user:diagnostic_password@localhost/mira"),
    )


def read_log(settings: Settings) -> str:
    return (settings.log_directory / config.LOG_FILENAME).read_text(encoding="utf-8")


def test_configures_stderr_and_persistent_file_sinks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = logging_settings(tmp_path)

    configure_logging(settings)
    logger.info("LOGGING_READY")

    assert "LOGGING_READY" in capsys.readouterr().err
    assert "LOGGING_READY" in read_log(settings)


def test_no_console_stream_still_configures_sanitized_persistent_logging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = logging_settings(tmp_path)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "__stderr__", None)

    configure_logging(settings)
    logger.error(r"NO_CONSOLE password=secret-value C:\Users\alice\private.txt")
    content = read_log(settings)

    assert "NO_CONSOLE" in content
    assert "secret-value" not in content
    assert r"C:\Users\alice" not in content


def test_reconfiguration_is_idempotent_and_does_not_duplicate_messages(
    tmp_path: Path,
) -> None:
    settings = logging_settings(tmp_path)

    configure_logging(settings)
    logger.info("FIRST_EVENT")
    configure_logging(settings)
    logger.info("SECOND_EVENT")
    content = read_log(settings)

    assert content.count("FIRST_EVENT") == 1
    assert content.count("SECOND_EVENT") == 1


def test_file_output_is_utf8_utc_and_contains_source_context(tmp_path: Path) -> None:
    settings = logging_settings(tmp_path)

    configure_logging(settings)
    logger.info("Türkçe yaşam")
    content = read_log(settings)

    assert "Türkçe yaşam" in content
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", content)
    assert "test_logging:test_file_output_is_utf8_utc_and_contains_source_context" in content


def test_file_output_has_no_ansi_escape_sequences(tmp_path: Path) -> None:
    settings = logging_settings(tmp_path)

    configure_logging(settings)
    logger.warning("NO_COLOR")

    assert "\x1b[" not in read_log(settings)


def test_exception_logging_keeps_class_and_frames_but_not_sensitive_value(
    tmp_path: Path,
) -> None:
    settings = logging_settings(tmp_path)
    configure_logging(settings)

    try:
        raise RuntimeError(r"password=secret-value at C:\Users\alice\private.txt")
    except RuntimeError:
        logger.exception("SAFE_FAILURE")
    content = read_log(settings)

    assert "SAFE_FAILURE" in content
    assert "RuntimeError" in content
    assert "secret-value" not in content
    assert r"C:\Users\alice" not in content


def test_runtime_paths_credentials_and_identifiers_are_redacted(tmp_path: Path) -> None:
    settings = logging_settings(tmp_path)
    configure_logging(settings)
    identifier = "12345678-1234-4234-8234-123456789abc"

    logger.error(
        "failure {} {} {}",
        settings.database_path,
        settings.database_url,
        identifier,
    )
    content = read_log(settings)

    assert "<DATABASE_PATH>" in content
    assert "<DATABASE_URL>" in content
    assert "<IDENTIFIER>" in content
    assert "diagnostic_password" not in content
    assert identifier not in content


def test_explicit_generated_incident_reference_is_preserved_but_other_ids_are_redacted(
    tmp_path: Path,
) -> None:
    settings = logging_settings(tmp_path)
    configure_logging(settings)
    incident_id = str(uuid4())
    unrelated_id = str(uuid4())

    logger.bind(safe_incident_id=incident_id).critical(
        "error_incident incident_id={} unrelated_id={}",
        incident_id,
        unrelated_id,
    )
    content = read_log(settings)

    assert incident_id in content
    assert unrelated_id not in content
    assert "<IDENTIFIER>" in content


def test_rotation_and_retention_are_finite() -> None:
    assert config.LOG_ROTATION_BYTES == 5 * 1024 * 1024
    assert config.LOG_RETENTION_DAYS == 14


def test_missing_log_directory_is_a_typed_actionable_failure(tmp_path: Path) -> None:
    settings = logging_settings(tmp_path)
    settings.log_directory.rmdir()

    with pytest.raises(ConfigurationError, match="check the log directory"):
        configure_logging(settings)

    assert not (settings.log_directory / config.LOG_FILENAME).exists()
