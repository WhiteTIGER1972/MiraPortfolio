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

- Define infrastructure ORM entities and explicit domain-to-entity mappers.
- Add concrete repository implementations and Alembic migrations.
- Implement repository-backed calculation and snapshot use cases before connecting the dashboard to live data.

## Delivered Commits

- `9ca313a` — `feat(core): add Sprint 1.1 configuration and database foundation`
- `7b58642` — `feat(ui): add Sprint 1.1 dashboard shell and theme`
- `d261d23` — `feat(app): add Sprint 1.1 application bootstrap`
- `eaa04d3` — `feat(domain): establish Sprint 1.2 domain foundation`
- `5a70345` — `build(project): add persistence scaffolding and quality tooling`
