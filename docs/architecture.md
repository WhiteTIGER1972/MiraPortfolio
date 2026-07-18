# Architecture

## Overview

Mira Portfolio follows a layered desktop architecture with `app/application/bootstrap.py` as its composition root. The domain remains independent, while the bootstrap and dependency container wire concrete UI and infrastructure components.

```text
app.application.bootstrap -> app.core / app.infrastructure / app.ui
app.application.events -> app.domain
app.infrastructure.persistence -> app.application / app.domain
app.ui -> app.core
app.core.container -> app.infrastructure
app.infrastructure -> app.core
app.domain -> Python standard library / app.domain
```

## Layers

- `app/core`: configuration defaults, Pydantic settings, logging, errors, and dependency composition.
- `app/application`: application bootstrap plus synchronous, in-process event orchestration.
- `app/domain`: persistence-agnostic business entities, value objects, immutable domain-event types, service and repository extension points, and domain errors.
- `app/infrastructure`: SQLAlchemy engine lifecycle, ORM models and mappers, repositories, Unit of Work, SQLite pragmas, and Alembic integration.
- `app/repositories`: reusable SQLAlchemy repository implementation; provider-specific adapters will remain outside domain.
- `app/ui`: programmatic PySide6 presentation layer and design system.

## Domain Persistence Boundary

Domain code uses only business concepts and standard-library value types. It must not import SQLAlchemy or declare ORM metadata. Infrastructure ORM models use explicit mappers across this boundary:

```text
TransactionModel (infrastructure / ORM)
        -> explicit mapper
Transaction (domain entity)
```

This boundary is intended to keep the domain independent of SQLite, PostgreSQL, and SQL Server.

## Persistence Foundation

Sprint 1.4 places SQLAlchemy implementations under
`app.infrastructure.persistence.sqlalchemy`. Typed ORM models cover `Asset`, `Portfolio`,
portfolio-owned `Transaction` history, `PriceHistory`, and `Snapshot`; the schema tables
are `assets`, `portfolios`, `portfolio_assets`, `transactions`, `price_history`, and
`snapshots`. SQLite is the current local persistence implementation.

ORM models and domain entities remain separate. Explicit mappers preserve domain-owned
UUIDs, exact `Decimal` values, UTC timestamps, flags, asset ordering, and transaction
ordering. Reconstruction executes public domain constructors, `Portfolio.add_asset`, and
`Portfolio.record_transaction`; it does not manipulate private state or bypass domain
validation. Transactions remain owned by the portfolio aggregate, and there is no
standalone transaction repository.

`ExactDecimal` stores finite financial values as SQLite text (`VARCHAR(100)`) without a
Python `float` conversion, preserving precision and trailing zeros. `UTCDateTime` accepts
only timezone-aware UTC values, stores canonical ISO 8601 text (`VARCHAR(40)`), and
reconstructs datetimes as aware UTC.

`SQLAlchemyAssetRepository`, `SQLAlchemyPortfolioRepository`,
`SQLAlchemyPriceHistoryRepository`, and `SQLAlchemySnapshotRepository` support `add`,
`get`, `list`, `delete`, `exists`, and `count`. Each receives an injected SQLAlchemy
`Session`, returns domain entities, and may flush to surface constraints. Repositories
never create, commit, roll back, or close their session and never publish events.

The application-layer `UnitOfWork` protocol is framework-independent.
`SQLAlchemyUnitOfWork` accepts an injected session factory, creates one session per
context, and shares it across all four repositories. `commit()` and `rollback()` are
explicit. Successful context exit does not commit; pending work and exceptional exits
roll back, exceptions propagate unchanged, and the session closes deterministically.

### Deferred Persistence Work

Post-commit event publication, an outbox, `PortfolioMetrics` persistence, a standalone
transaction repository, application services, portfolio calculations, automatic snapshot
generation, dashboard/UI integration, API persistence, cloud synchronization, and mobile
storage are not implemented.

## Domain Events

Sprint 1.3 is implemented. It keeps the existing immutable `DomainEvent` base, which
supplies a UUID and UTC occurrence timestamp, and adds concrete immutable event types.
Event payloads contain domain data only; financial values use `Decimal`,
`EventReasonCode` provides stable locale-independent machine-readable reasons, and event
definitions have no persistence, infrastructure, or UI dependencies.

`EventHandler` and `EventPublisher` are typed application protocols. `EventDispatcher`
depends on `EventPublisher` and forwards events without modifying them.
`InProcessEventBus` provides synchronous, instance-local, deterministic, exact-type
delivery within one application process. It preserves handler registration order, ignores
duplicate registration of the same handler for the same event type, and treats an event
without handlers as a no-op. Delivery is fail-fast: the first handler exception stops the
current dispatch and propagates unchanged.

Handler registration is explicit and requires a supplied bus instance. There is no global
bus, singleton, import-time registration, asynchronous or threaded dispatch, background
worker, external broker, or persistent event transport.

The implemented registered flow is exactly:

```text
TransactionAdded -> PortfolioUpdated
```

Automatic production of `SnapshotCreated` and `DashboardRefreshRequested` remains
deferred. Persistence-triggered event publication, repository-backed portfolio
calculations, and UI refresh integration also remain future work. The application does
not invent a total portfolio value or register UI event handlers.

## Client and Localization Direction

Mira Portfolio is intended to support desktop, iOS, and Android clients, with multilingual
presentation across all clients. This is a forward-looking architectural requirement, not
a completed feature. Domain and application layers must remain platform-independent, and
localized user-facing text must stay outside domain event payloads. Locale selection,
currency presentation, date formatting, and number formatting belong to presentation
layers.

## Tests

Tests live under the root `tests/` directory, and pytest uses `testpaths = ["tests"]`.
The focused Sprint 1.4 persistence suite contains 60 tests, and the full suite contains
117 tests. Persistence tests use temporary SQLite databases and do not touch the
repository database file.

## Database Lifecycle

`DatabaseManager` owns the SQLAlchemy engine and session factory. It exposes initialization, health checks, an Alembic migration hook, and graceful shutdown. SQLite connections enable foreign keys, WAL journaling, and normal synchronous mode.

Alembic revision `20260718_0001` creates exactly the six Sprint 1.4 tables with matching
primary keys, named foreign keys, checks, uniqueness constraints, and explicit
`CASCADE`/`RESTRICT` behavior. Upgrade, downgrade to base, repeat upgrade, and ORM metadata
parity are validated against isolated file-backed SQLite databases.
Persistence does not publish domain events; outbox processing and post-commit dispatch
remain deferred.

## Financial Precision

Financial calculations must use `Decimal`; Python `float` is prohibited in `app/`. The `scripts/check_no_floats.py` pre-commit check rejects float literals, annotations, and conversions before changes are committed.
