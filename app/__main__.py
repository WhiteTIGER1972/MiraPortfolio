"""Application module entry point."""

import sys

from app.application.bootstrap import create_application


def main() -> int:
    """Create and run the desktop application."""
    return create_application().exec()


if __name__ == "__main__":
    sys.exit(main())
