"""Tests for the Qt event-dispatch fatal boundary."""

import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThread
from PySide6.QtWidgets import QApplication

from app.application.resilience import ErrorIncident, IncidentPhase
from app.infrastructure.resilience import GlobalErrorBoundary
from app.ui.application import MiraApplication


class EventReceiver(QObject):
    """Record normal events and optionally raise from dispatch."""

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.fail = fail
        self.event_count = 0

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.User:
            self.event_count += 1
            if self.fail:
                raise RuntimeError("private Qt event value")
            return True
        return super().event(event)


def test_normal_qt_event_passes_through_unchanged(qapplication: QApplication) -> None:
    assert isinstance(qapplication, MiraApplication)
    receiver = EventReceiver()

    QApplication.sendEvent(receiver, QEvent(QEvent.Type.User))

    assert receiver.event_count == 1


def test_qt_event_exception_creates_one_fatal_incident(qapplication: QApplication) -> None:
    assert isinstance(qapplication, MiraApplication)
    boundary = GlobalErrorBoundary(
        incident_logger=lambda _incident: None,
        dialog_presenter=lambda _incident, _directory: None,
        exit_requester=lambda _code: None,
    )
    qapplication.bind_error_boundary(boundary)
    receiver = EventReceiver(fail=True)

    delivered = QApplication.sendEvent(receiver, QEvent(QEvent.Type.User))

    assert not delivered
    incident = boundary.fatal_incident
    assert incident is not None
    assert incident.phase is IncidentPhase.RUNTIME_QT_EVENT
    assert "private Qt event value" not in repr(incident)


def test_background_fatal_presentation_is_marshaled_to_qt_thread(
    qapplication: QApplication,
) -> None:
    incidents: list[ErrorIncident] = []
    dialog_threads: list[QThread] = []

    def present(_: ErrorIncident, __: Path | None) -> None:
        dialog_threads.append(QThread.currentThread())

    boundary = GlobalErrorBoundary(
        incident_logger=incidents.append,
        dialog_presenter=present,
        exit_requester=lambda _code: None,
    )
    boundary.bind_qt_application(qapplication)
    thread = threading.Thread(
        target=lambda: boundary.handle_exception(
            RuntimeError("private background Qt value"),
            phase=IncidentPhase.RUNTIME_BACKGROUND_THREAD,
        )
    )

    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert dialog_threads == []
    qapplication.processEvents()

    assert len(incidents) == 1
    assert dialog_threads == [qapplication.thread()]
