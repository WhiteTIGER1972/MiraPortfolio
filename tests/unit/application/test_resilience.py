"""Tests for immutable privacy-safe error incident records."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.application.resilience import (
    ErrorIncident,
    IncidentPhase,
    IncidentSeverity,
    create_error_incident,
)


def captured_incident(message: str = "private failure") -> ErrorIncident:
    def sensitive_operation() -> None:
        raise RuntimeError(message)

    try:
        sensitive_operation()
    except RuntimeError as error:
        return create_error_incident(error, IncidentPhase.STARTUP_CONTAINER)
    raise AssertionError("The test exception was not raised.")


def test_incident_and_frames_are_immutable() -> None:
    incident = captured_incident()

    with pytest.raises(FrozenInstanceError):
        setattr(incident, "safe_summary", "changed")
    with pytest.raises(FrozenInstanceError):
        setattr(incident.safe_frames[0], "filename", "changed.py")


def test_incident_identity_is_a_canonical_uuid() -> None:
    incident = captured_incident()

    assert UUID(str(incident.incident_id)) == incident.incident_id
    assert str(UUID(str(incident.incident_id))) == str(incident.incident_id)


def test_incident_timestamp_is_timezone_aware_utc() -> None:
    incident = captured_incident()

    assert incident.occurred_at_utc.tzinfo is not None
    assert incident.occurred_at_utc.utcoffset() == UTC.utcoffset(incident.occurred_at_utc)


def test_raw_exception_message_credentials_urls_and_paths_are_absent() -> None:
    secret = (
        r"password=hunter2 token=abc at C:\Users\alice\private\portfolio.db "
        "postgresql://alice:credential@example.test/mira"
    )

    incident = captured_incident(secret)
    rendered = repr(incident)

    assert incident.exception_type == "RuntimeError"
    assert secret not in rendered
    assert "hunter2" not in rendered
    assert "credential" not in rendered
    assert r"C:\Users\alice" not in rendered
    assert "postgresql://" not in rendered


def test_safe_frames_keep_only_useful_basenames_functions_and_lines() -> None:
    incident = captured_incident()

    assert incident.safe_frames
    assert any(frame.function_name == "sensitive_operation" for frame in incident.safe_frames)
    assert all(frame.filename == "test_resilience.py" for frame in incident.safe_frames)
    assert all(frame.line_number > 0 for frame in incident.safe_frames)
    assert all(
        "/" not in frame.filename and "\\" not in frame.filename for frame in incident.safe_frames
    )


def test_fields_and_deep_tracebacks_are_bounded() -> None:
    exception_type = type("X" * 400, (Exception,), {})

    def recurse(depth: int) -> None:
        if depth == 0:
            raise exception_type("sensitive")
        recurse(depth - 1)

    try:
        recurse(40)
    except exception_type as error:
        incident = create_error_incident(error, IncidentPhase.RUNTIME_MAIN_THREAD)
    else:
        raise AssertionError("The test exception was not raised.")

    assert len(incident.exception_type) == 128
    assert len(incident.safe_summary) <= 200
    assert len(incident.safe_frames) == 16
    assert all(len(frame.module_name) <= 96 for frame in incident.safe_frames)
    assert all(len(frame.function_name) <= 96 for frame in incident.safe_frames)
    assert all(len(frame.filename) <= 128 for frame in incident.safe_frames)


def test_direct_construction_rejects_non_utc_and_source_paths() -> None:
    incident = captured_incident()

    with pytest.raises(ValueError, match="UTC"):
        ErrorIncident(
            incident_id=incident.incident_id,
            occurred_at_utc=datetime.now(timezone(timedelta(hours=3))),
            phase=incident.phase,
            severity=IncidentSeverity.FATAL,
            exception_type=incident.exception_type,
            safe_summary=incident.safe_summary,
            safe_frames=incident.safe_frames,
        )


def test_summary_is_fixed_by_phase_not_exception_text() -> None:
    first = captured_incident("first private value")
    second = captured_incident("different private value")

    assert first.safe_summary == second.safe_summary
    assert "private" not in first.safe_summary.casefold()
