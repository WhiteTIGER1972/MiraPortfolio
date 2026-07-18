"""Tests for configuration defaults."""

from app.core.settings import Settings


def test_default_database_is_sqlite() -> None:
    """The desktop application uses SQLite by default."""
    assert Settings().database_url.startswith("sqlite")
