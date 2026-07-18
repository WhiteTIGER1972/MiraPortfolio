# Sprint Review

## Sprint 1.1 — Application Foundation

### Completed

- Established package layout, application bootstrap, configuration defaults, Pydantic Settings, logging, and exception hierarchy.
- Added `DatabaseManager` with engine, session factory, health check, migration hook, and shutdown lifecycle.
- Configured SQLite with `foreign_keys = ON`, `journal_mode = WAL`, and `synchronous = NORMAL`.
- Created the programmatic PySide6 design system and dashboard shell.

## Sprint 1.2 — Domain Foundation

### Completed

- Established domain folders for entities, value objects, events, services, repositories, and exceptions.
- Added persistence-agnostic `Asset`, `Portfolio`, `Transaction`, `PriceHistory`, and `Snapshot` entities.
- Added immutable financial and identifier value objects.
- Added the immutable `DomainEvent` base type; dispatch and application event orchestration followed in Sprint 1.3.
- Verified that the domain package has no SQLAlchemy or ORM references.

## Sprint 1.3 â€” Application Orchestration and Domain Events

### Completed

- Added immutable, validated `TransactionAdded`, `PortfolioUpdated`, `SnapshotCreated`, and `DashboardRefreshRequested` event types.
- Kept the existing `DomainEvent` base and added locale-independent, machine-readable `EventReasonCode` values.
- Added typed `EventHandler` and `EventPublisher` protocols plus an `EventDispatcher` that forwards through `EventPublisher`.
- Added a synchronous, deterministic `InProcessEventBus` with exact-type delivery, ordered handlers, duplicate prevention, unregistration, and instance-local state.
- Defined fail-fast delivery: the first handler exception stops dispatch and propagates unchanged.
- Added explicit handler registration with a supplied bus and no global bus or import-time registration.
- Implemented exactly the `TransactionAdded -> PortfolioUpdated` flow.
- Verified that domain events remain free of infrastructure, persistence, ORM, and UI dependencies.
- Centralized tests under root `tests/` with pytest `testpaths = ["tests"]`.

### Validation Status

- The full Sprint 1.3 suite passes 57 tests.
- Sprint 1.3 typing, no-float validation, and forbidden-import scanning pass.
- Repository-wide Ruff lint and formatting checks pass.

### Deferred

- Automatic production of `SnapshotCreated` and `DashboardRefreshRequested`.
- Repository integration, persistence, and portfolio calculations.
- UI refresh integration and presentation-layer event subscriptions.
- Asynchronous, threaded, distributed, or persistent event transport.

### Architecture Direction

- Mira Portfolio is intended to support desktop, iOS, and Android clients with multilingual presentation across all clients.
- Domain and application layers must remain platform-independent.
- Localized user-facing text must remain outside domain event payloads.
- Locale selection, currency presentation, date formatting, and number formatting are presentation concerns.
- These are forward-looking architectural requirements, not completed mobile or localization features.

### Next

- Implement repository-backed calculation and snapshot use cases before connecting the dashboard to live data.
- Define post-commit event collection and outbox requirements in a future sprint.

## Sprint 1.4 — Persistence Foundation

### Status

Completed.

### Goals Achieved

- Implemented the SQLAlchemy persistence foundation for assets, portfolios, ordered portfolio assets, portfolio-owned transactions, price history, and snapshots.
- Added explicit domain/ORM mappers and four domain-facing repository implementations.
- Added a framework-independent `UnitOfWork` protocol and concrete SQLAlchemy transaction boundary.
- Added Alembic revision `20260718_0001` and validated its full lifecycle.
- Added isolated unit, integration, migration, and architecture tests.

### Key Technical Decisions

- `ExactDecimal` stores financial values as SQLite text (`VARCHAR(100)`), never `REAL` or `FLOAT`, and preserves precision and trailing zeros.
- `UTCDateTime` stores aware UTC values as canonical ISO 8601 text and reconstructs aware UTC datetimes.
- UUID creation and ownership remain in the domain.
- ORM models remain in infrastructure; domain reconstruction uses public constructors, `Portfolio.add_asset`, and `Portfolio.record_transaction` without private-state manipulation or validation bypass.
- Repositories receive an injected session and never own commit, rollback, or close behavior.
- `SQLAlchemyUnitOfWork` creates one session per context and shares it across `assets`, `portfolios`, `price_history`, and `snapshots`.
- Commit and rollback are explicit. Successful context exit does not auto-commit; uncommitted and exceptional work rolls back, and the session closes deterministically.
- Transactions remain portfolio-owned; there is no standalone transaction repository.

### Validation Status

- The focused persistence suite passes 60 tests; the full suite passes 117 tests.
- Alembic upgrade, downgrade to base, repeat upgrade, and ORM metadata comparison pass.
- All persistence tests use temporary SQLite databases; the repository database remains untouched.
- Ruff, Sprint 1.4 MyPy, no-float, architecture, import-boundary, and schema consistency checks pass.

### Known Limitations and Deferred Work

- Event publication after commit, post-commit dispatch, transactional outbox, and messaging.
- `PortfolioMetrics` persistence and a standalone transaction repository.
- Application services, transaction workflows, portfolio calculations, and automatic snapshot generation.
- Desktop UI integration and dashboard refresh.
- API persistence, cloud synchronization, mobile storage, and mobile clients.

### Next Sprint

Sprint 1.5 — Application / Portfolio Engine.

Planned focus:

- Portfolio application use cases and transaction workflows.
- Position and weighted-average-cost calculations.
- Realized and unrealized profit and loss.
- Portfolio valuation.
- Application-service orchestration through `UnitOfWork`.

## Delivered Commits

- `9ca313a` — `feat(core): add Sprint 1.1 configuration and database foundation`
- `7b58642` — `feat(ui): add Sprint 1.1 dashboard shell and theme`
- `d261d23` — `feat(app): add Sprint 1.1 application bootstrap`
- `eaa04d3` — `feat(domain): establish Sprint 1.2 domain foundation`
- `5a70345` — `build(project): add persistence scaffolding and quality tooling`
