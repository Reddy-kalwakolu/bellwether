# ADR-0002: `create_all` over Alembic, for now

**Date:** 2026-07-20
**Status:** Accepted

## Context
campaign-service is the first service with a schema. Production systems manage schema changes with versioned migrations, and Alembic is the default choice for SQLAlchemy. But right now the substrate has exactly one writer, no deployed data anyone depends on, and a schema that will churn daily as Days 3–5 add services. Migration files written against a schema that changes every day are noise, not safety.

## Decision
Create tables at startup with `Base.metadata.create_all(engine)`. No migration tool yet.

## Alternatives considered
- **Alembic from day one:** rejected for now. It buys nothing while the schema has no consumers to protect, and every daily change would cost a migration file that is never applied to real data.
- **Hand-written SQL DDL:** rejected. Duplicates the model definitions with no upside.

## Consequences
- Startup is one line, the test suite builds the same schema against SQLite, and Day 3–5 model changes cost nothing.
- The trade is real: `create_all` never alters an existing table. Changing a column today means dropping the volume (`docker compose down -v`).

**The trigger that flips this decision:** the first time a second service reads these tables, or the first time we need data to survive a schema change. At that point Alembic goes in, with the current schema as the initial revision.
