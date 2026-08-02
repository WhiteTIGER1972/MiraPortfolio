"""Tests for the application-facing diagnostics contract."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.application.diagnostics import DiagnosticsService, SupportBundleRecord


def test_diagnostics_service_is_a_narrow_abstract_contract() -> None:
    assert DiagnosticsService.__abstractmethods__ == frozenset({"create_support_bundle"})
    with pytest.raises(TypeError):
        DiagnosticsService()


def test_support_bundle_record_is_immutable() -> None:
    record = SupportBundleRecord(
        path=Path("bundle.mirasupport"),
        filename="bundle.mirasupport",
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        bundle_id=UUID("12345678-1234-4234-8234-123456789abc"),
        application_version="0.1.0",
        member_count=5,
        archive_size_bytes=1024,
        archive_sha256="a" * 64,
    )

    with pytest.raises(FrozenInstanceError):
        record.filename = "changed.mirasupport"
