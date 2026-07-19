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
- Position and moving weighted average cost calculations.
- Realized and unrealized P&L.
- Portfolio valuation.
- Application-service orchestration through `UnitOfWork`.

## Sprint 1.5 — Application / Portfolio Engine

### Status

Completed.

### Sprint Objective

Build framework-independent application orchestration and a portfolio engine on top of
the Sprint 1.4 persistence foundation.

### Delivered

- Immutable command, query, and result DTOs plus the
  `PortfolioApplicationService` contract and `DefaultPortfolioApplicationService`.
- UUID-based Asset identity for buy and sell workflows.
- Explicit application repository ports and typed `UnitOfWork` repository properties.
- Public, aggregate-owned transaction removal through `Portfolio.remove_transaction`.
- SQLAlchemy aggregate save/reconciliation and UUID-based Asset and Portfolio deletion.
- Transactional create, buy, sell, delete, get, and list application workflows.
- A pure moving weighted average cost `PortfolioPositionCalculator`.
- Fee-aware cost basis and realized P&L using exact `Decimal` arithmetic.
- A pure `PortfolioValuationCalculator` with externally supplied prices and separate
  per-currency totals.
- Cross-layer acceptance tests using the real application service, `SQLAlchemyUnitOfWork`,
  repositories, isolated persistence reload, position calculator, and valuation
  calculator.

### Architectural Decisions

- Asset UUID, not symbol, identifies an Asset in application workflows. Symbols are
  normalized descriptive data but are not globally unique.
- `DefaultPortfolioApplicationService` receives a `Callable[[], UnitOfWork]` and creates
  one `UnitOfWork` per operation.
- Successful writes commit explicitly once; reads do not commit; failed work is handled
  by `UnitOfWork` rollback behavior.
- Repositories may flush but do not commit or roll back. `add` creates a new aggregate,
  while `save` persists an existing aggregate and does not upsert a missing Portfolio.
- Portfolio owns its transaction lifecycle, including public removal that preserves
  remaining order and Asset membership.
- Moving weighted average cost is the accepted position-accounting method.
- BUY commission and tax are capitalized into acquisition cost; SELL commission and tax
  reduce net proceeds and realized P&L.
- Only BUY and SELL are calculated. Income and corporate-action transaction types fail
  explicitly with `UnsupportedTransactionTypeError`.
- Valuation uses externally supplied, UUID-keyed prices in each Asset's own currency.
- Without an FX model, currency values remain in separate buckets and no cross-currency
  portfolio total is exposed.

### Challenges Discovered

- Symbols could not support a safe singular lookup: neither symbol nor symbol plus
  currency is guaranteed unique, so commands were corrected to use Asset UUID.
- Portfolio initially had no public transaction-removal method; implementation stopped
  until aggregate-owned removal and a domain-specific absence error were defined.
- There was no explicit repository save/update contract for loaded aggregates; public
  repository ports and distinct `add`/`save` semantics were established first.
- Concrete Asset and Portfolio delete signatures initially accepted entities rather
  than the UUID identity required by the ports.
- The initial save task described a `None` return, while the accepted port requires
  `save(portfolio: Portfolio) -> Portfolio`; the accepted contract remained authoritative.
- Commission and tax treatment was initially unspecified. The accepted policy
  capitalizes BUY charges and deducts SELL charges from proceeds.
- Dividend, rights issue, bonus issue, and stock split accounting lacked sufficient
  semantics and metadata. They remain unsupported rather than being silently ignored or
  calculated from assumptions.

At each compatibility gate, implementation stopped until a safe contract or accounting
policy was established. No symbol-uniqueness assumption, private aggregate mutation,
persistence shortcut, or undefined corporate-action calculation was introduced.

### Validation Summary

- Full suite: 233 tests passed.
- New portfolio-engine integration suite: 11 tests passed.
- Domain suite: 105 tests passed.
- Application/workflow suite: 30 tests passed.
- Repository/UnitOfWork suite: 33 tests passed.
- Migration suite: 5 tests passed.
- Repository-wide Ruff lint and formatting checks passed.
- Relevant changed-file and Sprint 1.5 strict MyPy scopes passed; no claim is made that
  unrelated legacy repository-wide MyPy findings were resolved.
- Architecture, import-boundary, no-float, direct-SQL, and private-mutation policy scans
  passed.
- Isolated test databases were used; development and production database fingerprints
  remained unchanged.

### Deferred Work

- Dividend and income accounting.
- Rights issue processing and valuation.
- Bonus issue processing.
- Stock split and reverse-split processing.
- FX rates, currency conversion, and base-currency portfolio totals.
- Market-price loading from `PriceHistoryRepository`, including stale-price and
  price-source policy.
- Snapshot generation.
- Allocation percentages, percentage and annualized returns, and benchmark comparison.
- An application-level calculated portfolio query or facade.
- UI integration and bootstrap/composition wiring for the portfolio engine.
- Event/outbox publication.
- Application-level oversell prevalidation, if later desired.

### Sprint Acceptance

Sprint 1.5 is complete when documentation validation passes, the full suite remains at
233 passing tests or higher, no unrelated files are staged, and the working tree is
clean after the final documentation commit.

## Delivered Commits

- `9ca313a` — `feat(core): add Sprint 1.1 configuration and database foundation`
- `7b58642` — `feat(ui): add Sprint 1.1 dashboard shell and theme`
- `d261d23` — `feat(app): add Sprint 1.1 application bootstrap`
- `eaa04d3` — `feat(domain): establish Sprint 1.2 domain foundation`
- `5a70345` — `build(project): add persistence scaffolding and quality tooling`
