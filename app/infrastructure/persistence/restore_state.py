"""Typed state transitions for restart-safe database restore transactions."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from app.core.exceptions import RestoreApplicationError


class RestoreOperationState(StrEnum):
    """Durable states written to the restore operation journal."""

    PREPARED = "prepared"
    ORIGINAL_PRESERVED = "original_preserved"
    RESTORED_INSTALLED = "restored_installed"
    VERIFIED = "verified"


VALID_TRANSITIONS: Final = {
    RestoreOperationState.PREPARED: RestoreOperationState.ORIGINAL_PRESERVED,
    RestoreOperationState.ORIGINAL_PRESERVED: RestoreOperationState.RESTORED_INSTALLED,
    RestoreOperationState.RESTORED_INSTALLED: RestoreOperationState.VERIFIED,
}


def validate_operation_transition(
    current: RestoreOperationState,
    requested: RestoreOperationState,
) -> None:
    """Reject repeated, backward, and skipped normal-operation transitions."""
    expected = VALID_TRANSITIONS.get(current)
    if requested is not expected:
        raise RestoreApplicationError(
            f"Restore transaction cannot transition from {current.value!r} to {requested.value!r}."
        )


__all__ = [
    "RestoreOperationState",
    "VALID_TRANSITIONS",
    "validate_operation_transition",
]
