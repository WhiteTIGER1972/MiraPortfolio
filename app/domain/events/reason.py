"""Stable, locale-independent reason codes for domain events."""

from enum import StrEnum


class EventReasonCode(StrEnum):
    """Identify event causes without carrying user-facing display text."""

    TRANSACTION_ADDED = "transaction_added"
    SNAPSHOT_CREATED = "snapshot_created"
