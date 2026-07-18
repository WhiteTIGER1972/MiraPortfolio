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
- Added the immutable `DomainEvent` base type; event dispatch and an event bus are not implemented.
- Verified that the domain package has no SQLAlchemy or ORM references.

### Next

- Define infrastructure ORM entities and explicit domain-to-entity mappers.
- Add concrete repository implementations and Alembic migrations.
- Implement application use cases and connect the dashboard to live data.

## Delivered Commits

- `9ca313a` — `feat(core): add Sprint 1.1 configuration and database foundation`
- `7b58642` — `feat(ui): add Sprint 1.1 dashboard shell and theme`
- `d261d23` — `feat(app): add Sprint 1.1 application bootstrap`
- `eaa04d3` — `feat(domain): establish Sprint 1.2 domain foundation`
- `5a70345` — `build(project): add persistence scaffolding and quality tooling`
