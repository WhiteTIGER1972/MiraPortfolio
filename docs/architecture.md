# Architecture

## Overview

Mira Portfolio follows a layered desktop architecture with `app/application/bootstrap.py` as its composition root. The domain remains independent, while the bootstrap and dependency container wire concrete UI and infrastructure components.

```text
app.application.bootstrap -> app.core / app.infrastructure / app.ui
app.ui -> app.core
app.core.container -> app.infrastructure
app.infrastructure -> app.core
app.domain -> Python standard library / app.domain
```

## Layers

- `app/core`: configuration defaults, Pydantic settings, logging, errors, and dependency composition.
- `app/application`: application bootstrap; use-case orchestration is planned.
- `app/domain`: persistence-agnostic business entities, value objects, a base domain-event type, service and repository extension points, and domain errors.
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

`DomainEvent` provides only an immutable base fact type with an identifier and UTC occurrence timestamp. Event dispatch, an event bus, subscriptions, and application-layer event handling have not been implemented.

## Database Lifecycle

`DatabaseManager` owns the SQLAlchemy engine and session factory. It exposes initialization, health checks, an Alembic migration hook, and graceful shutdown. SQLite connections enable foreign keys, WAL journaling, and normal synchronous mode. `alembic.ini` is currently a placeholder; an Alembic environment and migration revisions have not been implemented.

## Financial Precision

Financial calculations must use `Decimal`; Python `float` is prohibited in `app/`. The `scripts/check_no_floats.py` pre-commit check rejects float literals, annotations, and conversions before changes are committed.
