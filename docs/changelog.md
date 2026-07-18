# Changelog

All notable project changes are recorded in this document.

## Unreleased

### Added

- PySide6 application bootstrap and premium dashboard shell.
- Pydantic Settings configuration with platform-aware application directories.
- Loguru logging, dependency container, exception hierarchy, and SQLAlchemy database lifecycle manager.
- SQLite foreign-key, WAL, and synchronous PRAGMA configuration.
- Pure domain entities for assets, portfolios, transactions, price history, and snapshots.
- Immutable domain value objects: money, percentages, ticker, ISIN, currency code, and risk score.
- Base domain-event infrastructure plus service, repository, and exception extension points; event dispatch and an event bus are not implemented.
- Generic SQLAlchemy repository foundation and Alembic configuration placeholder.
- Development tooling configuration for Black, Ruff, MyPy, pytest, and pre-commit.
