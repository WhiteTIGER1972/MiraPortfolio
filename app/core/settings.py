"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core import config, runtime_paths


def _path_setting(values: dict[str, object], field_name: str) -> Path:
    value = values[field_name]
    if not isinstance(value, Path):
        raise TypeError(f"{field_name} must be a Path.")
    return value


def _default_database_directory(values: dict[str, object]) -> Path:
    return _path_setting(values, "data_directory") / config.DATABASE_DIRECTORY_NAME


def _default_export_directory(values: dict[str, object]) -> Path:
    return _path_setting(values, "data_directory") / config.EXPORT_DIRECTORY_NAME


def _default_backup_directory(values: dict[str, object]) -> Path:
    return _path_setting(values, "data_directory") / config.BACKUP_DIRECTORY_NAME


def _default_database_path(values: dict[str, object]) -> Path:
    return _path_setting(values, "database_directory") / config.DATABASE_FILENAME


def _default_database_url(values: dict[str, object]) -> str:
    return runtime_paths.sqlite_url_for_path(_path_setting(values, "database_path"))


class Settings(BaseSettings):
    """Load Mira Portfolio settings from environment variables and an optional .env file.

    Each setting supports the ``MIRA_`` environment prefix. For example,
    ``MIRA_THEME=light`` overrides the default theme.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MIRA_", extra="ignore")

    app_name: str = config.APP_NAME
    company_name: str = config.COMPANY_NAME
    app_version: str = config.VERSION
    theme: str = config.THEME
    language: str = config.LANGUAGE
    default_currency: str = config.DEFAULT_CURRENCY
    auto_backup: bool = config.AUTO_BACKUP
    auto_snapshot: bool = config.AUTO_SNAPSHOT
    update_interval: int = Field(
        default=config.UPDATE_INTERVAL,
        gt=0,
        description="Market refresh interval in seconds.",
    )
    data_directory: Path = Field(default_factory=lambda: runtime_paths.default_data_directory())
    cache_directory: Path = Field(default_factory=lambda: runtime_paths.default_cache_directory())
    database_directory: Path = Field(default_factory=_default_database_directory)
    export_directory: Path = Field(default_factory=_default_export_directory)
    backup_directory: Path = Field(default_factory=_default_backup_directory)
    log_directory: Path = Field(default_factory=lambda: runtime_paths.default_log_directory())
    database_path: Path = Field(default_factory=_default_database_path)
    database_url: str = Field(default_factory=_default_database_url)
    environment: str = config.ENVIRONMENT
    debug: bool = config.DEBUG
    log_level: str = config.LOG_LEVEL


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""
    return Settings()
