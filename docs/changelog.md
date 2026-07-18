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
- Immutable `TransactionAdded`, `PortfolioUpdated`, `SnapshotCreated`, and `DashboardRefreshRequested` domain events with validated UUID, UTC, `Decimal`, and locale-independent reason-code payloads.
- Sprint 1.3 event architecture is implemented with typed `EventHandler` and `EventPublisher` protocols, `EventDispatcher`, explicit registration, and synchronous instance-local, exact-type, deterministic, fail-fast delivery without global state or import-time registration.
- Implemented `TransactionAdded -> PortfolioUpdated` orchestration. Automatic `SnapshotCreated` and `DashboardRefreshRequested` production, repository integration, persistence, portfolio calculations, UI refresh integration, and async or threaded dispatch remain deferred.
- Centralized tests under root `tests/` with pytest `testpaths = ["tests"]`; the full Sprint 1.3 suite passes 57 tests.
- Documented the future requirement for platform-independent domain and application layers supporting desktop, iOS, and Android clients with multilingual presentation; localization and locale-sensitive formatting remain presentation concerns rather than completed features.
- Domain service, repository, and exception extension points.
- Generic SQLAlchemy repository foundation and Alembic configuration placeholder.
- Development tooling configuration for Black, Ruff, MyPy, pytest, and pre-commit.
