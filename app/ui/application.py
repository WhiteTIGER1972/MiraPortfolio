"""Qt application subclass providing the desktop event-dispatch boundary."""

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication

from app.application.resilience import IncidentPhase
from app.infrastructure.resilience import GlobalErrorBoundary


class MiraApplication(QApplication):
    """Route uncaught Python event exceptions into the fatal boundary."""

    def __init__(
        self,
        arguments: Sequence[str],
        error_boundary: GlobalErrorBoundary,
    ) -> None:
        super().__init__(list(arguments))
        self._error_boundary = error_boundary

    def bind_error_boundary(self, error_boundary: GlobalErrorBoundary) -> None:
        """Rebind a reused application instance to the active process boundary."""
        self._error_boundary = error_boundary

    def notify(self, receiver: QObject, event: QEvent) -> bool:
        """Preserve normal dispatch and terminate after an unhandled event error."""
        try:
            return super().notify(receiver, event)
        except Exception as error:
            self._error_boundary.handle_exception(
                error,
                phase=IncidentPhase.RUNTIME_QT_EVENT,
            )
            return False


__all__ = ["MiraApplication"]
