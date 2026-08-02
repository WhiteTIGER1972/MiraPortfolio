"""Process-wide error hooks and privacy-safe fatal shutdown orchestration."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, TextIO

from loguru import logger
from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication

from app.application.resilience import (
    ErrorIncident,
    IncidentPhase,
    IncidentSeverity,
    create_error_incident,
)
from app.core import config

if TYPE_CHECKING:
    from sys import UnraisableHookArgs

IncidentLogger = Callable[[ErrorIncident], None]
DialogPresenter = Callable[[ErrorIncident, Path | None], None]
ExitRequester = Callable[[int], None]

_EXCLUDED_EXCEPTIONS = (KeyboardInterrupt, SystemExit, GeneratorExit)
_FALLBACK_LIMIT = 512


class _QtFatalRelay(QObject):
    """Marshal fatal presentation from a worker onto the Qt application thread."""

    fatal_requested = Signal(object)

    def __init__(self, callback: Callable[[ErrorIncident], None]) -> None:
        super().__init__()
        self._callback = callback
        self.fatal_requested.connect(self._deliver)

    def request(self, incident: ErrorIncident) -> None:
        self.fatal_requested.emit(incident)

    @Slot(object)
    def _deliver(self, incident: object) -> None:
        if isinstance(incident, ErrorIncident):
            self._callback(incident)


class GlobalErrorBoundary:
    """Own global hooks and terminate safely after one unhandled fatal error."""

    def __init__(
        self,
        *,
        incident_logger: IncidentLogger | None = None,
        dialog_presenter: DialogPresenter | None = None,
        exit_requester: ExitRequester | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._phase = IncidentPhase.STARTUP_SETTINGS
        self._fatal_incident: ErrorIncident | None = None
        self._handling_unraisable = False
        self._installed = False
        self._logging_available = False
        self._log_directory: Path | None = None
        self._qt_application: QApplication | None = None
        self._emergency_application: QApplication | None = None
        self._qt_relay: _QtFatalRelay | None = None
        self._incident_logger = incident_logger
        self._dialog_presenter = dialog_presenter
        self._exit_requester = exit_requester
        self._stderr = stderr if stderr is not None else sys.stderr

        self._original_sys_excepthook: (
            Callable[[type[BaseException], BaseException, TracebackType | None], None] | None
        ) = None
        self._original_threading_excepthook: Callable[[threading.ExceptHookArgs], object] | None = (
            None
        )
        self._original_unraisablehook: Callable[[UnraisableHookArgs], None] | None = None
        self._sys_hook = self._on_sys_exception
        self._threading_hook = self._on_threading_exception
        self._unraisable_hook = self._on_unraisable_exception

    @property
    def current_phase(self) -> IncidentPhase:
        """Return the most recently entered startup or runtime phase."""
        with self._lock:
            return self._phase

    @property
    def fatal_incident(self) -> ErrorIncident | None:
        """Return the first fatal incident owned by this boundary, if any."""
        with self._lock:
            return self._fatal_incident

    @property
    def original_sys_excepthook(
        self,
    ) -> Callable[[type[BaseException], BaseException, TracebackType | None], None] | None:
        """Expose the retained main-thread hook for lifecycle verification."""
        return self._original_sys_excepthook

    @property
    def original_threading_excepthook(
        self,
    ) -> Callable[[threading.ExceptHookArgs], object] | None:
        """Expose the retained thread hook for lifecycle verification."""
        return self._original_threading_excepthook

    @property
    def original_unraisablehook(self) -> Callable[[UnraisableHookArgs], None] | None:
        """Expose the retained unraisable hook for lifecycle verification."""
        return self._original_unraisablehook

    def enter_phase(self, phase: IncidentPhase) -> None:
        """Set the active phase immediately before entering a startup operation."""
        with self._lock:
            self._phase = phase

    def mark_logging_available(self, log_directory: Path) -> None:
        """Record that persistent logging completed successfully."""
        with self._lock:
            self._logging_available = True
            self._log_directory = log_directory

    def bind_qt_application(self, application: QApplication) -> None:
        """Bind fatal presentation and exit requests to an existing GUI application."""
        relay = _QtFatalRelay(self._finish_fatal_handling)
        relay.moveToThread(application.thread())
        with self._lock:
            self._qt_application = application
            self._qt_relay = relay

    def install_process_hooks(self) -> None:
        """Install owned process hooks once while retaining their originals."""
        with self._lock:
            if self._installed:
                return
            self._original_sys_excepthook = sys.excepthook
            self._original_threading_excepthook = threading.excepthook
            self._original_unraisablehook = sys.unraisablehook
            sys.excepthook = self._sys_hook
            threading.excepthook = self._threading_hook
            sys.unraisablehook = self._unraisable_hook
            self._installed = True

    def restore_process_hooks(self) -> None:
        """Restore only hooks still owned by this boundary instance."""
        with self._lock:
            if not self._installed:
                return
            if sys.excepthook is self._sys_hook and self._original_sys_excepthook is not None:
                sys.excepthook = self._original_sys_excepthook
            if (
                threading.excepthook is self._threading_hook
                and self._original_threading_excepthook is not None
            ):
                threading.excepthook = self._original_threading_excepthook
            if (
                sys.unraisablehook is self._unraisable_hook
                and self._original_unraisablehook is not None
            ):
                sys.unraisablehook = self._original_unraisablehook
            self._installed = False

    def handle_exception(
        self,
        error: BaseException,
        *,
        phase: IncidentPhase | None = None,
        error_traceback: TracebackType | None = None,
    ) -> bool:
        """Record and terminate for the first non-control-flow fatal exception."""
        if isinstance(error, _EXCLUDED_EXCEPTIONS):
            return False
        try:
            incident = create_error_incident(
                error,
                phase or self.current_phase,
                error_traceback=error_traceback,
            )
            with self._lock:
                first_incident = self._fatal_incident
                if first_incident is None:
                    self._fatal_incident = incident
                    owner = True
                else:
                    owner = False
            if not owner:
                assert first_incident is not None
                self._write_suppressed_fallback(first_incident)
                return False
            self._record_incident(incident)
            self._dispatch_fatal_handling(incident)
            return True
        except Exception:
            self._write_handler_failure_fallback()
            self._request_exit_safely()
            return False

    def handle_unraisable(
        self,
        error: BaseException,
        *,
        error_traceback: TracebackType | None = None,
    ) -> bool:
        """Record one non-fatal interpreter cleanup failure without showing UI."""
        if isinstance(error, _EXCLUDED_EXCEPTIONS):
            return False
        with self._lock:
            if self._handling_unraisable:
                return False
            self._handling_unraisable = True
        try:
            incident = create_error_incident(
                error,
                IncidentPhase.RUNTIME_UNRAISABLE,
                severity=IncidentSeverity.NON_FATAL,
                error_traceback=error_traceback,
            )
            self._record_incident(incident)
            return True
        except Exception:
            self._write_handler_failure_fallback()
            return False
        finally:
            with self._lock:
                self._handling_unraisable = False

    def _on_sys_exception(
        self,
        exception_type: type[BaseException],
        error: BaseException,
        error_traceback: TracebackType | None,
    ) -> None:
        if issubclass(exception_type, _EXCLUDED_EXCEPTIONS):
            original = self._original_sys_excepthook
            if original is not None:
                original(exception_type, error, error_traceback)
            return
        self.handle_exception(
            error,
            phase=IncidentPhase.RUNTIME_MAIN_THREAD,
            error_traceback=error_traceback,
        )

    def _on_threading_exception(self, arguments: threading.ExceptHookArgs) -> None:
        error = arguments.exc_value
        if error is None:
            self._write_handler_failure_fallback()
            return
        if isinstance(error, _EXCLUDED_EXCEPTIONS):
            original = self._original_threading_excepthook
            if original is not None:
                original(arguments)
            return
        self.handle_exception(
            error,
            phase=IncidentPhase.RUNTIME_BACKGROUND_THREAD,
            error_traceback=arguments.exc_traceback,
        )

    def _on_unraisable_exception(self, arguments: UnraisableHookArgs) -> None:
        error = arguments.exc_value
        if error is None:
            self._call_original_unraisable(arguments)
            return
        if isinstance(error, _EXCLUDED_EXCEPTIONS):
            self._call_original_unraisable(arguments)
            return
        try:
            handled = self.handle_unraisable(
                error,
                error_traceback=arguments.exc_traceback,
            )
        except Exception:
            handled = False
        if not handled:
            self._call_original_unraisable(arguments)

    def _call_original_unraisable(self, arguments: UnraisableHookArgs) -> None:
        original = self._original_unraisablehook
        if original is not None:
            original(arguments)

    def _record_incident(self, incident: ErrorIncident) -> None:
        if self._incident_logger is not None:
            try:
                self._incident_logger(incident)
            except Exception:
                self._write_incident_fallback(incident)
            return

        with self._lock:
            logging_available = self._logging_available
        if not logging_available:
            self._write_incident_fallback(incident)
            return
        try:
            frame_text = ",".join(
                f"{frame.module_name}:{frame.function_name}:{frame.filename}:{frame.line_number}"
                for frame in incident.safe_frames
            )
            event_name = (
                "fatal_unhandled_error"
                if incident.severity is IncidentSeverity.FATAL
                else "nonfatal_unraisable_error"
            )
            logger.bind(safe_incident_id=str(incident.incident_id)).critical(
                "error_incident event={} incident_id={} severity={} phase={} "
                "exception={} frames={}",
                event_name,
                incident.incident_id,
                incident.severity.value,
                incident.phase.value,
                incident.exception_type,
                frame_text or "none",
            )
        except Exception:
            self._write_incident_fallback(incident)

    def _dispatch_fatal_handling(self, incident: ErrorIncident) -> None:
        with self._lock:
            application = self._qt_application
            relay = self._qt_relay
        if (
            application is not None
            and relay is not None
            and QThread.currentThread() is not application.thread()
        ):
            relay.request(incident)
            return
        self._finish_fatal_handling(incident)

    def _finish_fatal_handling(self, incident: ErrorIncident) -> None:
        try:
            if self._dialog_presenter is not None:
                self._dialog_presenter(incident, self._log_directory)
            else:
                application = self._ensure_qt_application(incident.phase)
                if application is not None:
                    self._present_dialog(incident)
                else:
                    self._write_incident_fallback(incident)
        except Exception:
            self._write_incident_fallback(incident)
        self._request_exit_safely(incident)

    def _ensure_qt_application(self, phase: IncidentPhase) -> QApplication | None:
        with self._lock:
            if self._qt_application is not None:
                return self._qt_application
        existing = QCoreApplication.instance()
        if isinstance(existing, QApplication):
            self.bind_qt_application(existing)
            return existing
        if existing is not None or phase is IncidentPhase.STARTUP_QT:
            return None
        try:
            application = QApplication([])
            application.setApplicationName(config.APP_NAME)
            self._emergency_application = application
            self.bind_qt_application(application)
        except Exception:
            return None
        return application

    def _present_dialog(self, incident: ErrorIncident) -> None:
        from app.ui.dialogs.fatal_error_dialog import FatalErrorDialog

        dialog = FatalErrorDialog(incident, self._log_directory)
        dialog.exec()

    def _request_exit_safely(self, incident: ErrorIncident | None = None) -> None:
        try:
            if self._exit_requester is not None:
                self._exit_requester(1)
            elif QCoreApplication.instance() is not None:
                QCoreApplication.exit(1)
        except Exception:
            if incident is None:
                self._write_handler_failure_fallback()
            else:
                self._write_incident_fallback(incident)

    def _write_incident_fallback(self, incident: ErrorIncident) -> None:
        severity = "fatal error" if incident.severity is IncidentSeverity.FATAL else "error"
        message = (
            f"{config.APP_NAME} {severity} | incident={incident.incident_id} | "
            f"utc={incident.occurred_at_utc.isoformat()} | phase={incident.phase.value} | "
            f"exception={incident.exception_type} | details may be available in application logs"
        )
        self._write_bounded_stderr(message)

    def _write_suppressed_fallback(self, incident: ErrorIncident) -> None:
        self._write_bounded_stderr(
            f"{config.APP_NAME} additional fatal error suppressed | "
            f"incident={incident.incident_id} | phase={incident.phase.value}"
        )

    def _write_handler_failure_fallback(self) -> None:
        self._write_bounded_stderr(
            f"{config.APP_NAME} fatal error | incident handling unavailable | "
            "application must close"
        )

    def _write_bounded_stderr(self, message: str) -> None:
        bounded = message[: _FALLBACK_LIMIT - 1] + "\n"
        try:
            self._stderr.write(bounded)
            self._stderr.flush()
        except Exception:
            return


__all__ = ["GlobalErrorBoundary"]
