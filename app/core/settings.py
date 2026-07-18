"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path

from platformdirs import user_cache_dir, user_data_dir
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core import config


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
    cache_directory: Path = Field(
        default_factory=lambda: (
            Path(user_cache_dir(config.APP_NAME, config.COMPANY_NAME)) / config.CACHE_DIRECTORY_NAME
        )
    )
    database_directory: Path = Field(
        default_factory=lambda: (
            Path(user_data_dir(config.APP_NAME, config.COMPANY_NAME))
            / config.DATABASE_DIRECTORY_NAME
        )
    )
    export_directory: Path = Field(
        default_factory=lambda: (
            Path(user_data_dir(config.APP_NAME, config.COMPANY_NAME)) / config.EXPORT_DIRECTORY_NAME
        )
    )
    backup_directory: Path = Field(
        default_factory=lambda: (
            Path(user_data_dir(config.APP_NAME, config.COMPANY_NAME)) / config.BACKUP_DIRECTORY_NAME
        )
    )
    environment: str = config.ENVIRONMENT
    debug: bool = config.DEBUG
    database_url: str = config.DATABASE_URL
    log_level: str = config.LOG_LEVEL


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""
    return Settings()
