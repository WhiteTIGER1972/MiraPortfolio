"""Unit coverage for database preparation values and Alembic configuration."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.infrastructure.persistence.alembic_support import (
    DEFAULT_MIGRATION_SCRIPT_LOCATION,
    create_alembic_config,
)
from app.infrastructure.persistence.database_preparation import (
    DatabasePreparationResult,
    PreparationOutcome,
)


def test_preparation_result_is_small_and_immutable() -> None:
    result = DatabasePreparationResult(PreparationOutcome.CREATED)

    assert result.outcome is PreparationOutcome.CREATED
    with pytest.raises(FrozenInstanceError):
        result.outcome = PreparationOutcome.UPGRADED


def test_alembic_configuration_preserves_target_url_and_uses_absolute_paths(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'percent%20name.db').as_posix()}"

    config = create_alembic_config(database_url)

    assert config.get_main_option("sqlalchemy.url") == database_url
    assert Path(config.get_main_option("script_location")).is_absolute()
    assert Path(config.get_main_option("prepend_sys_path")).is_absolute()


def test_alembic_configuration_accepts_injected_script_location(tmp_path: Path) -> None:
    script_location = tmp_path / "test-migrations"

    config = create_alembic_config(
        f"sqlite:///{(tmp_path / 'database.db').as_posix()}",
        script_location=script_location,
    )

    assert Path(config.get_main_option("script_location")) == script_location.resolve()
    assert DEFAULT_MIGRATION_SCRIPT_LOCATION.is_absolute()
