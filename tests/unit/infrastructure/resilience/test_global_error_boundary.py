"""Tests for process hooks, duplicate suppression, and safe fatal fallback."""

from __future__ import annotations

import io
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.resilience import ErrorIncident, IncidentPhase, IncidentSeverity
from app.infrastructure.resilience import GlobalErrorBoundary


class BoundaryHarness:
    """Collect only privacy-safe boundary outputs."""

    def __init__(self) -> None:
        self.incidents: list[ErrorIncident] = []
        self.dialogs: list[ErrorIncident] = []
        self.exits: list[int] = []
        self.stderr = io.StringIO()
        self.boundary = GlobalErrorBoundary(
            incident_logger=self.incidents.append,
            dialog_presenter=self._present,
            exit_requester=self.exits.append,
            stderr=self.stderr,
        )

    def _present(self, incident: ErrorIncident, _: Path | None) -> None:
        self.dialogs.append(incident)


def raised_error(message: str = "sensitive failure") -> RuntimeError:
    try:
        raise RuntimeError(message)
    except RuntimeError as error:
        return error


def test_hook_installation_is_idempotent_retains_originals_and_restores() -> None:
    boundary = GlobalErrorBoundary()
    original_sys = sys.excepthook
    original_threading = threading.excepthook
    original_unraisable = sys.unraisablehook
    try:
        boundary.install_process_hooks()
        installed = (sys.excepthook, threading.excepthook, sys.unraisablehook)
        boundary.install_process_hooks()

        assert (sys.excepthook, threading.excepthook, sys.unraisablehook) == installed
        assert boundary.original_sys_excepthook is original_sys
        assert boundary.original_threading_excepthook is original_threading
        assert boundary.original_unraisablehook is original_unraisable
    finally:
        boundary.restore_process_hooks()

    assert sys.excepthook is original_sys
    assert threading.excepthook is original_threading
    assert sys.unraisablehook is original_unraisable


def test_restore_does_not_overwrite_a_hook_owned_elsewhere() -> None:
    boundary = GlobalErrorBoundary()

    def replacement(_: type[BaseException], __: BaseException, ___: object) -> None:
        return

    boundary.install_process_hooks()
    sys.excepthook = replacement
    try:
        boundary.restore_process_hooks()
        assert sys.excepthook is replacement
    finally:
        sys.excepthook = boundary.original_sys_excepthook or sys.__excepthook__


@pytest.mark.parametrize("control_flow", (KeyboardInterrupt(), SystemExit(), GeneratorExit()))
def test_control_flow_exceptions_are_not_converted(control_flow: BaseException) -> None:
    harness = BoundaryHarness()

    assert not harness.boundary.handle_exception(control_flow)
    assert harness.incidents == []
    assert harness.dialogs == []
    assert harness.exits == []


def test_sys_excepthook_creates_one_main_thread_fatal_incident() -> None:
    harness = BoundaryHarness()
    error = raised_error(r"password=hunter2 C:\Users\alice\private.txt")
    harness.boundary.install_process_hooks()
    try:
        sys.excepthook(type(error), error, error.__traceback__)
    finally:
        harness.boundary.restore_process_hooks()

    assert len(harness.incidents) == 1
    assert harness.incidents[0].phase is IncidentPhase.RUNTIME_MAIN_THREAD
    assert harness.incidents[0].severity is IncidentSeverity.FATAL
    assert harness.dialogs == harness.incidents
    assert harness.exits == [1]
    assert "hunter2" not in repr(harness.incidents[0])


def test_threading_excepthook_is_fatal_and_requests_exit() -> None:
    harness = BoundaryHarness()
    harness.boundary.install_process_hooks()

    def fail() -> None:
        raise RuntimeError("private background value")

    thread = threading.Thread(target=fail, name="resilience-test")
    try:
        thread.start()
        thread.join(timeout=5)
    finally:
        harness.boundary.restore_process_hooks()

    assert not thread.is_alive()
    assert len(harness.incidents) == 1
    assert harness.incidents[0].phase is IncidentPhase.RUNTIME_BACKGROUND_THREAD
    assert harness.dialogs == harness.incidents
    assert harness.exits == [1]


def test_unraisable_hook_records_nonfatal_without_dialog_or_exit() -> None:
    harness = BoundaryHarness()
    error = raised_error("private destructor value")
    arguments = SimpleNamespace(exc_value=error, exc_traceback=error.__traceback__)
    harness.boundary.install_process_hooks()
    try:
        sys.unraisablehook(arguments)
    finally:
        harness.boundary.restore_process_hooks()

    assert len(harness.incidents) == 1
    assert harness.incidents[0].phase is IncidentPhase.RUNTIME_UNRAISABLE
    assert harness.incidents[0].severity is IncidentSeverity.NON_FATAL
    assert harness.dialogs == []
    assert harness.exits == []


def test_second_fatal_is_suppressed_without_a_second_log_dialog_or_exit() -> None:
    harness = BoundaryHarness()

    assert harness.boundary.handle_exception(raised_error("first secret"))
    assert not harness.boundary.handle_exception(raised_error("second secret"))

    assert len(harness.incidents) == 1
    assert len(harness.dialogs) == 1
    assert harness.exits == [1]
    fallback = harness.stderr.getvalue()
    assert "additional fatal error suppressed" in fallback
    assert "second secret" not in fallback


def test_independent_boundaries_do_not_share_fatal_state() -> None:
    first = BoundaryHarness()
    second = BoundaryHarness()

    assert first.boundary.handle_exception(raised_error("first"))
    assert second.boundary.handle_exception(raised_error("second"))

    assert len(first.incidents) == 1
    assert len(second.incidents) == 1
    assert first.incidents[0].incident_id != second.incidents[0].incident_id


def test_logger_failure_falls_back_without_raw_exception_data() -> None:
    stderr = io.StringIO()

    def fail_logging(_: ErrorIncident) -> None:
        raise RuntimeError("logger private value")

    boundary = GlobalErrorBoundary(
        incident_logger=fail_logging,
        dialog_presenter=lambda _incident, _directory: None,
        exit_requester=lambda _code: None,
        stderr=stderr,
    )

    boundary.handle_exception(raised_error(r"password=hunter2 C:\Users\alice\private.txt"))
    output = stderr.getvalue()

    assert len(output) <= 512
    assert "RuntimeError" in output
    assert "hunter2" not in output
    assert r"C:\Users\alice" not in output
    assert "logger private value" not in output


def test_missing_interpreter_stderr_streams_do_not_recurse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exits: list[int] = []

    def fail_logging(_: ErrorIncident) -> None:
        raise RuntimeError("private logger failure")

    def fail_dialog(_: ErrorIncident, __: Path | None) -> None:
        raise RuntimeError("private dialog failure")

    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "__stderr__", None)
    boundary = GlobalErrorBoundary(
        incident_logger=fail_logging,
        dialog_presenter=fail_dialog,
        exit_requester=exits.append,
    )

    assert boundary.handle_exception(raised_error("private root failure"))
    assert boundary.fatal_incident is not None
    assert exits == [1]


@pytest.mark.parametrize("failing_surface", ("dialog", "exit"))
def test_dialog_or_exit_failure_falls_back_without_recursion(failing_surface: str) -> None:
    incidents: list[ErrorIncident] = []
    stderr = io.StringIO()

    def present(_: ErrorIncident, __: Path | None) -> None:
        if failing_surface == "dialog":
            raise RuntimeError("dialog private value")

    def request_exit(_: int) -> None:
        if failing_surface == "exit":
            raise RuntimeError("exit private value")

    boundary = GlobalErrorBoundary(
        incident_logger=incidents.append,
        dialog_presenter=present,
        exit_requester=request_exit,
        stderr=stderr,
    )

    boundary.handle_exception(raised_error("original private value"))

    assert len(incidents) == 1
    assert boundary.fatal_incident is incidents[0]
    assert "private value" not in stderr.getvalue()
    assert len(stderr.getvalue()) <= 512


def test_concurrent_fatal_calls_produce_one_owner_without_deadlock() -> None:
    harness = BoundaryHarness()
    start = threading.Barrier(9)

    def invoke(index: int) -> None:
        start.wait(timeout=5)
        harness.boundary.handle_exception(raised_error(f"private {index}"))

    threads = [threading.Thread(target=invoke, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(harness.incidents) == 1
    assert len(harness.dialogs) == 1
    assert harness.exits == [1]
    assert "private" not in harness.stderr.getvalue()


def test_process_control_flow_hook_delegates_to_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegated: list[type[BaseException]] = []

    def original(
        exception_type: type[BaseException],
        _: BaseException,
        __: object,
    ) -> None:
        delegated.append(exception_type)

    monkeypatch.setattr(sys, "excepthook", original)
    boundary = GlobalErrorBoundary()
    boundary.install_process_hooks()
    try:
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    finally:
        boundary.restore_process_hooks()

    assert delegated == [KeyboardInterrupt]
