"""Unit tests for the explicit restore transaction state machine."""

from __future__ import annotations

import pytest

from app.core.exceptions import RestoreApplicationError
from app.infrastructure.persistence.restore_state import (
    RestoreOperationState,
    validate_operation_transition,
)


@pytest.mark.parametrize(
    ("current", "requested"),
    (
        (
            RestoreOperationState.PREPARED,
            RestoreOperationState.ORIGINAL_PRESERVED,
        ),
        (
            RestoreOperationState.ORIGINAL_PRESERVED,
            RestoreOperationState.RESTORED_INSTALLED,
        ),
        (
            RestoreOperationState.RESTORED_INSTALLED,
            RestoreOperationState.VERIFIED,
        ),
    ),
)
def test_each_documented_normal_transition_is_valid(
    current: RestoreOperationState,
    requested: RestoreOperationState,
) -> None:
    validate_operation_transition(current, requested)


@pytest.mark.parametrize(
    ("current", "requested"),
    tuple(
        (current, requested)
        for current in RestoreOperationState
        for requested in RestoreOperationState
        if (current, requested)
        not in {
            (
                RestoreOperationState.PREPARED,
                RestoreOperationState.ORIGINAL_PRESERVED,
            ),
            (
                RestoreOperationState.ORIGINAL_PRESERVED,
                RestoreOperationState.RESTORED_INSTALLED,
            ),
            (
                RestoreOperationState.RESTORED_INSTALLED,
                RestoreOperationState.VERIFIED,
            ),
        }
    ),
)
def test_repeated_backward_and_skipped_transitions_are_rejected(
    current: RestoreOperationState,
    requested: RestoreOperationState,
) -> None:
    with pytest.raises(RestoreApplicationError, match="cannot transition"):
        validate_operation_transition(current, requested)
