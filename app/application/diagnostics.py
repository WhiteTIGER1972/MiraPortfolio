"""Application-facing contract and immutable result for support diagnostics."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SupportBundleRecord:
    """Describe one verified privacy-safe support bundle."""

    path: Path
    filename: str
    created_at: datetime
    bundle_id: UUID
    application_version: str
    member_count: int
    archive_size_bytes: int
    archive_sha256: str


class DiagnosticsService(ABC):
    """Create support diagnostics without exposing infrastructure details."""

    @abstractmethod
    def create_support_bundle(self) -> SupportBundleRecord:
        """Create and return one atomically installed, verified support bundle."""


__all__ = ["DiagnosticsService", "SupportBundleRecord"]
