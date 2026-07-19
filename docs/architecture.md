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
- `app/application`: application bootstrap, use-case contracts and orchestration, repository
  ports, `UnitOfWork` boundary, and synchronous in-process event orchestration.
- `app/domain`: persistence-agnostic business entities, value objects, immutable domain-event types, service and repository extension points, and domain errors.
- `app/infrastructure`: SQLAlchemy engine lifecycle, ORM models and mappers, repositories, `SQLAlchemyUnitOfWork`, SQLite pragmas, and Alembic integration.
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
transaction repository, automatic snapshot generation, dashboard/UI integration, API
persistence, cloud synchronization, and mobile storage are not implemented.

## Sprint 1.5 Portfolio Engine

Sprint 1.5 adds framework-independent application orchestration and pure portfolio
analytics on top of the Sprint 1.4 persistence boundary.

### Application Contracts and Orchestration

The application input contracts are immutable command and query DTOs:

- Commands: `CreatePortfolioCommand`, `BuyAssetCommand`, `SellAssetCommand`, and
  `DeleteTransactionCommand`.
- Queries: `GetPortfolioQuery` and `ListPortfoliosQuery`.
- Results: `PortfolioSummary`, `PortfolioDetails`, `TransactionView`, and
  `AssetPositionView`.

Despite its current name, `AssetPositionView` contains descriptive `Asset` data only:
identity, symbol, name, type, currency, active state, and creation time. It does not
contain calculated quantity, average cost, cost basis, market value, realized P&L, or
unrealized P&L.

`DefaultPortfolioApplicationService` receives a `Callable[[], UnitOfWork]` through
constructor injection and obtains one new `UnitOfWork` for each operation. Successful
create, buy, sell, and delete workflows explicitly call `commit()` exactly once after
all mutation and persistence work succeeds. Get and list workflows never commit.
Rollback and resource cleanup remain the responsibility of the `UnitOfWork` context.
The service translates only absent portfolios and assets to `PortfolioNotFoundError`
and `AssetNotFoundError`; domain and repository exceptions otherwise propagate.

Transaction commands identify an existing Asset with `asset_id: UUID`. A symbol is
normalized descriptive data, not identity: symbols are not globally unique, persistence
allows duplicates, and symbol plus currency is not guaranteed unique. `Asset.id` is the
canonical transaction identity.

The application-layer repository ports are `AssetRepository`, `PortfolioRepository`,
`PriceHistoryRepository`, and `SnapshotRepository`. They replace the former private
generic repository protocol in `UnitOfWork`, whose repository properties now expose
these explicit types. `add` introduces a new aggregate; `save` persists changes to an
existing aggregate. The accepted portfolio contract is:

```python
save(portfolio: Portfolio) -> Portfolio
```

`save` is not an upsert. A missing persistence target raises `RepositoryError`; a
successful call returns a domain `Portfolio`, does not expose an ORM model, and does not
commit internally.

### Portfolio Mutation and Persistence Reconciliation

`Portfolio.remove_transaction(transaction_id: UUID) -> None` removes exactly one owned
transaction, preserves the relative order of the remaining transactions, and leaves the
portfolio's Assets unchanged. An unknown identifier raises
`TransactionNotFoundError`. Removal does not publish a new domain event.

`SQLAlchemyPortfolioRepository.save` reconciles portfolio state without taking over the
transaction boundary. It matches transactions by UUID, persists additions and supported
field updates, removes transactions absent from the aggregate, and preserves transaction
and Asset ordering. Portfolio-to-Asset associations resolve existing Asset rows only;
save neither updates nor deletes independently persisted Asset data. Unsupported Asset
association removal is rejected.

Reconciliation avoids duplicate Portfolio, Transaction, Asset, and association rows.
The repository may flush to validate and stage changes, but it never commits or rolls
back, so all work remains reversible through `UnitOfWork.rollback()` or exceptional
context exit. Concrete Asset and Portfolio delete operations also use UUID identity.

### Application Workflow

The runtime dependency flow is:

```text
UI / future adapter
        |
        v
Command or Query DTO
        |
        v
DefaultPortfolioApplicationService
        |
        v
UnitOfWork -> Repository Port
                    |
                    v
          SQLAlchemy Repository
                    |
                    v
             Domain Aggregate
```

Write workflows extend that flow explicitly:

```text
Domain mutation
        |
        v
Repository.add / Repository.save
        |
        v
UnitOfWork.commit
```

The portfolio service and application contracts do not import infrastructure.
Infrastructure is supplied at the composition boundary through the injected
`UnitOfWork` factory.

### Position Calculation

The pure position API is:

```python
PortfolioPositionCalculator.calculate(
    portfolio: Portfolio,
) -> tuple[AssetPosition, ...]
```

`AssetPosition` is immutable and contains `asset_id`, `quantity`, `average_cost`,
`cost_basis`, and `realized_pnl`. The calculator processes the canonical
`Portfolio.transactions` order, calculates each Asset independently with moving
weighted average cost, returns positions in first-transaction-appearance order, retains
closed positions, and omits portfolio Assets with no transaction history.

For a BUY:

```text
gross cost = quantity × unit price
acquisition cost = gross cost + commission + tax
new cost basis = current cost basis + acquisition cost
new average cost = new cost basis / new quantity
```

BUY commission and tax increase cost basis and average cost; they do not directly change
realized P&L.

For a SELL:

```text
gross proceeds = quantity × unit price
net proceeds = gross proceeds - commission - tax
disposed cost = quantity × current average cost
realized P&L change = net proceeds - disposed cost
```

SELL commission and tax reduce net proceeds and realized P&L. They do not change the
average cost of a remaining open position. A full close resets quantity, average cost,
and cost basis to exact `Decimal` zero while preserving cumulative realized P&L. A later
BUY starts a new acquisition-cost cycle without discarding prior realized P&L.

All arithmetic uses `Decimal`. Zero transaction prices are valid.
`Transaction.calculate_total()` is deliberately not used because its gross-plus-charges
formula does not express the required SELL proceeds policy.

Only `TransactionType.BUY` and `TransactionType.SELL` are supported. Dividends, rights
issues, bonus issues, stock splits, and any other unsupported type raise
`UnsupportedTransactionTypeError` at their canonical history position; they are never
silently ignored. An oversell or sell-before-buy history raises
`InsufficientPositionError`. This is a calculation-validity rule, not a Sprint 1.5
application-service precondition for selling.

### Portfolio Valuation

Valuation composes the accepted position calculator rather than duplicating transaction
accounting:

```python
PortfolioValuationCalculator.calculate(
    portfolio: Portfolio,
    market_prices: Mapping[UUID, Decimal],
) -> PortfolioValuation
```

`ValuedAssetPosition` is immutable and contains `asset_id`, `currency`, `quantity`,
`average_cost`, `cost_basis`, `market_price`, `market_value`, `realized_pnl`,
`unrealized_pnl`, and `total_pnl`. For an open position:

```text
market value = quantity × market price
unrealized P&L = market value - cost basis
total P&L = realized P&L + unrealized P&L
```

A closed position remains included, consumes no current price, has
`market_price = None`, zero market value and unrealized P&L, and total P&L equal to
realized P&L.

`CurrencyValuation` contains `currency`, `cost_basis`, `market_value`, `realized_pnl`,
`unrealized_pnl`, and `total_pnl`. `PortfolioValuation` contains only immutable
`positions` and `currencies` tuples.

Current prices are supplied by the caller, keyed by Asset UUID, and denominated in the
Asset's own currency. The calculator performs no repository or price-history lookup.
An open position without a supplied price raises `MissingMarketPriceError`; a consumed
negative price raises `InvalidMarketPriceError`; zero is valid. Extra prices are ignored,
and closed positions do not consume supplied prices. An Asset position that cannot be
matched to public portfolio Asset metadata raises `PositionAssetNotFoundError`.

Each Asset is valued in its own currency. Totals are grouped into separate
`CurrencyValuation` buckets ordered by first currency appearance in position order.
There is no FX conversion, base-currency inference, or cross-currency portfolio grand
total. Values in different currencies must not be added together; this is a deliberate
correctness boundary.

Position and valuation results are immutable, result collections are tuples, and both
calculators are stateless. They perform no I/O, repository access, `UnitOfWork` access,
logging, persistence, or event publication. They do not mutate the input Portfolio or
price mapping and do not convert through `float`, round, quantize, or mutate the global
`Decimal` context.

### Cross-Layer Acceptance Validation

The Sprint 1.5 acceptance path uses real components:

```text
Application service
        -> SQLAlchemyUnitOfWork
        -> SQLAlchemy repositories
        -> isolated SQLite persistence
        -> domain-object reload
        -> position calculation
        -> valuation calculation
```

The integration suite validates create, buy, partial sell, full close, close and reopen,
transaction deletion, get and list, rollback, repeated-save deduplication, missing-target
save rejection, duplicate symbols resolved by UUID, fee-aware persistence and
calculation, zero-price transactions and valuation, oversell after reload, unsupported
transaction types after reload, multiple Assets, multiple currencies, closed-only
currency buckets, and empty portfolios. Production and development databases are not
used by these tests.

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
At Sprint 1.5 completion, the full suite contains 233 passing tests, including 11 new
cross-layer portfolio-engine integration tests. Integration and persistence tests use
temporary SQLite databases and do not touch development or production database files.

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
