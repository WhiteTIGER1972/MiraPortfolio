"""Application module entry point."""

import sys

from app.application.bootstrap import create_application_with_boundary
from app.application.resilience import IncidentPhase
from app.infrastructure.resilience import GlobalErrorBoundary


def main() -> int:
    """Create and run the desktop application."""
    return run_desktop_application(GlobalErrorBoundary())


def run_desktop_application(boundary: GlobalErrorBoundary) -> int:
    """Run guarded startup and return a controlled process exit code."""
    boundary.install_process_hooks()
    try:
        try:
            application = create_application_with_boundary(boundary)
        except Exception as error:
            boundary.handle_exception(error)
            return 1

        boundary.enter_phase(IncidentPhase.RUNTIME_MAIN_THREAD)
        try:
            return application.exec()
        except Exception as error:
            boundary.handle_exception(
                error,
                phase=IncidentPhase.RUNTIME_MAIN_THREAD,
            )
            return 1
    finally:
        boundary.restore_process_hooks()


if __name__ == "__main__":
    sys.exit(main())
