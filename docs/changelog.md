# Changelog

All notable project changes are recorded in this document.

## Unreleased

### Sprint 1.4 — Persistence Foundation

- Added `ExactDecimal` and `UTCDateTime` adapters for lossless SQLite text storage without monetary `REAL` columns or naive domain timestamps.
- Added six-table SQLAlchemy ORM metadata and explicit domain mappers that preserve UUIDs, exact values, UTC timestamps, aggregate ordering, and domain validation.
- Added `SQLAlchemyAssetRepository`, `SQLAlchemyPortfolioRepository`, `SQLAlchemyPriceHistoryRepository`, and `SQLAlchemySnapshotRepository` with injected-session ownership and `add`, `get`, `list`, `delete`, `exists`, and `count`.
- Added a framework-independent `UnitOfWork` protocol and `SQLAlchemyUnitOfWork` with one shared session, explicit commit/rollback, rollback on missing commit or exception, and deterministic close.
- Added Alembic revision `20260718_0001` for the six persistence tables, validated through upgrade, downgrade, repeat upgrade, and ORM metadata comparison.
- Added 60 isolated persistence tests; the full suite passes 117 tests using temporary SQLite databases.
- Automatic event publication, outbox processing, `PortfolioMetrics` persistence, standalone transaction repositories, portfolio calculations, snapshot generation, API/cloud/mobile persistence, and UI integration remain deferred.

### Added

- PySide6 application bootstrap and premium dashboard shell.
- Pydantic Settings configuration with platform-aware application directories.
- Loguru logging, dependency container, exception hierarchy, and SQLAlchemy database lifecycle manager.
- SQLite foreign-key, WAL, and synchronous PRAGMA configuration.
- Pure domain entities for assets, portfolios, transactions, price history, and snapshots.
- Immutable domain value objects: money, percentages, ticker, ISIN, currency code, and risk score.
- Immutable `TransactionAdded`, `PortfolioUpdated`, `SnapshotCreated`, and `DashboardRefreshRequested` domain events with validated UUID, UTC, `Decimal`, and locale-independent reason-code payloads.
- Sprint 1.3 event architecture is implemented with typed `EventHandler` and `EventPublisher` protocols, `EventDispatcher`, explicit registration, and synchronous instance-local, exact-type, deterministic, fail-fast delivery without global state or import-time registration.
- Implemented `TransactionAdded -> PortfolioUpdated` orchestration. At Sprint 1.3 completion, automatic `SnapshotCreated` and `DashboardRefreshRequested` production, repository integration, persistence, portfolio calculations, UI refresh integration, and async or threaded dispatch remained deferred.
- Centralized tests under root `tests/` with pytest `testpaths = ["tests"]`; the full Sprint 1.3 suite passes 57 tests.
- Documented the future requirement for platform-independent domain and application layers supporting desktop, iOS, and Android clients with multilingual presentation; localization and locale-sensitive formatting remain presentation concerns rather than completed features.
- Domain service, repository, and exception extension points.
- Generic SQLAlchemy repository foundation and operational Alembic environment.
- Development tooling configuration for Black, Ruff, MyPy, pytest, and pre-commit.
