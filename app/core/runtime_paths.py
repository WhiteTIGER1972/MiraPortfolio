"""Side-effect-free helpers for per-user application runtime paths."""

from pathlib import Path, PurePath

from platformdirs import user_cache_path, user_data_path, user_log_path
from sqlalchemy.engine import URL

from app.core import config


def default_data_directory() -> Path:
    """Return the platform-specific per-user application data root."""
    return user_data_path(
        config.APP_NAME,
        config.COMPANY_NAME,
        ensure_exists=False,
    )


def default_cache_directory() -> Path:
    """Return the platform-specific per-user application cache root."""
    return user_cache_path(
        config.APP_NAME,
        config.COMPANY_NAME,
        ensure_exists=False,
    )


def default_log_directory() -> Path:
    """Return the platform-specific per-user application log root."""
    return user_log_path(
        config.APP_NAME,
        config.COMPANY_NAME,
        ensure_exists=False,
    )


def preferences_directory(data_directory: PurePath) -> Path:
    """Return the deterministic preference directory without creating it."""
    return Path(data_directory) / config.PREFERENCES_DIRECTORY_NAME


def preferences_file(data_directory: PurePath) -> Path:
    """Return the deterministic versioned preference file path."""
    return preferences_directory(data_directory) / config.PREFERENCES_FILENAME


def sqlite_url_for_path(database_path: PurePath) -> str:
    """Build a SQLAlchemy SQLite URL without altering an absolute path."""
    if not database_path.is_absolute():
        raise ValueError("SQLite database paths must be absolute.")
    return URL.create(
        drivername="sqlite",
        database=str(database_path),
    ).render_as_string(hide_password=False)
