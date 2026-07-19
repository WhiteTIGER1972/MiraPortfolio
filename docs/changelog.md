# Changelog

All notable project changes are recorded in this document.

## Unreleased

### Sprint 1.5 — Application / Portfolio Engine

#### Added

- Added immutable application commands (`CreatePortfolioCommand`, `BuyAssetCommand`,
  `SellAssetCommand`, and `DeleteTransactionCommand`), queries (`GetPortfolioQuery` and
  `ListPortfoliosQuery`), and result DTOs (`PortfolioSummary`, `PortfolioDetails`,
  `TransactionView`, and the descriptive, non-calculated `AssetPositionView`).
- Added `DefaultPortfolioApplicationService` with an injected `UnitOfWork` factory,
  explicit one-commit write workflows, non-committing reads, and narrow translation of
  missing portfolios and Assets to `PortfolioNotFoundError` and `AssetNotFoundError`.
- Added explicit `AssetRepository`, `PortfolioRepository`, `PriceHistoryRepository`, and
  `SnapshotRepository` application ports.
- Added `Portfolio.remove_transaction`, preserving transaction order and Asset state and
  raising `TransactionNotFoundError` for an unknown owned transaction.
- Added `PortfolioPositionCalculator` and immutable `AssetPosition` results using moving
  weighted average cost, fee-aware BUY cost capitalization, fee-aware SELL realized P&L,
  exact `Decimal` arithmetic, closed-position retention, and explicit long-only history
  validation.
- Added `InsufficientPositionError` and `UnsupportedTransactionTypeError` for invalid
  position histories.
- Added `PortfolioValuationCalculator` with immutable `ValuedAssetPosition`,
  `CurrencyValuation`, and `PortfolioValuation` results, caller-supplied UUID-keyed
  prices, open/closed position policies, and separate per-currency aggregation.
- Added `MissingMarketPriceError`, `InvalidMarketPriceError`, and
  `PositionAssetNotFoundError` for valuation input and domain-consistency failures.
- Added 11 cross-layer integration tests spanning the real application service,
  `SQLAlchemyUnitOfWork` and repositories, isolated persistence reload, position
  calculation, and valuation.

#### Changed

- Changed buy and sell transaction identity from descriptive symbol to canonical
  `asset_id: UUID`; symbols remain normalized but are not assumed unique.
- Changed `UnitOfWork` repository properties from a private generic protocol to explicit
  repository port types.
- Changed concrete Asset and Portfolio repository delete operations to accept UUIDs.
- Added the distinct `PortfolioRepository.save(portfolio: Portfolio) -> Portfolio`
  contract and SQLAlchemy aggregate reconciliation for transaction additions, updates,
  removals, and ordered Asset associations. Save rejects missing targets rather than
  upserting and never commits internally.

#### Validation

- Full suite: 233 passed.
- New cross-layer integration tests: 11 passed.
- Domain tests: 105 passed.
- Application/workflow tests: 30 passed.
- Repository/UnitOfWork tests: 33 passed.
- Migration tests: 5 passed.
- Ruff, relevant changed-file and Sprint 1.5 strict MyPy scopes, architecture scans, and
  no-float/direct-SQL/private-mutation scans passed.
- Development and production database fingerprints remained unchanged.

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
