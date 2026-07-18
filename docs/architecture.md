# Architecture

## Overview

Mira Portfolio follows a layered desktop architecture with `app/application/bootstrap.py` as its composition root. The domain remains independent, while the bootstrap and dependency container wire concrete UI and infrastructure components.

```text
app.application.bootstrap -> app.core / app.infrastructure / app.ui
app.application.events -> app.domain
app.ui -> app.core
app.core.container -> app.infrastructure
app.infrastructure -> app.core
app.domain -> Python standard library / app.domain
```

## Layers

- `app/core`: configuration defaults, Pydantic settings, logging, errors, and dependency composition.
- `app/application`: application bootstrap plus synchronous, in-process event orchestration.
- `app/domain`: persistence-agnostic business entities, value objects, immutable domain-event types, service and repository extension points, and domain errors.
- `app/infrastructure`: SQLAlchemy engine lifecycle, SQLite pragmas, Alembic integration, and future ORM entities.
- `app/repositories`: reusable SQLAlchemy repository implementation; provider-specific adapters will remain outside domain.
- `app/ui`: programmatic PySide6 presentation layer and design system.

## Domain Persistence Boundary

Domain code uses only business concepts and standard-library value types. It must not import SQLAlchemy or declare ORM metadata. Future ORM models will use explicit mappers across this boundary:

```text
TransactionEntity (infrastructure / ORM)
        -> explicit mapper
Transaction (domain entity)
```

This boundary is intended to keep the domain independent of SQLite, PostgreSQL, and SQL Server.

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
deferred. Repository integration, persistence, portfolio calculations, and UI refresh
integration also remain future work. The application does not invent a total portfolio
value or register UI event handlers.

## Client and Localization Direction

Mira Portfolio is intended to support desktop, iOS, and Android clients, with multilingual
presentation across all clients. This is a forward-looking architectural requirement, not
a completed feature. Domain and application layers must remain platform-independent, and
localized user-facing text must stay outside domain event payloads. Locale selection,
currency presentation, date formatting, and number formatting belong to presentation
layers.

## Tests

Tests live under the root `tests/` directory, and pytest uses `testpaths = ["tests"]`.
The full suite at Sprint 1.3 completion contains 57 passing tests.

## Database Lifecycle

`DatabaseManager` owns the SQLAlchemy engine and session factory. It exposes initialization, health checks, an Alembic migration hook, and graceful shutdown. SQLite connections enable foreign keys, WAL journaling, and normal synchronous mode. `alembic.ini` is currently a placeholder; an Alembic environment and migration revisions have not been implemented.

## Financial Precision

Financial calculations must use `Decimal`; Python `float` is prohibited in `app/`. The `scripts/check_no_floats.py` pre-commit check rejects float literals, annotations, and conversions before changes are committed.
