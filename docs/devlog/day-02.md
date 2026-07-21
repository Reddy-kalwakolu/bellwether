# Day 2 — campaign-service

**Level:** 0 · **Date:** 2026-07-20

## Shipped
- `substrate/shared/logging.py` — structured JSON logging every service will reuse
- `campaign-service`: FastAPI + SQLAlchemy 2.0 over Postgres, 8 endpoints, OpenAPI at `/docs`
- Domain model: `Campaign` (budget, daily pacing budget, frequency cap, targeting, brand-safety exclusions, flight window) and `Creative`
- Typed error envelope `{"error": {"code", "message"}}` on every failure path, including validation errors
- Prometheus metrics (`http_requests_total`, `http_request_duration_seconds`) + per-request structured log lines with `latency_ms`
- Containerized, wired into Compose on host port 8001, registered as a Prometheus scrape target
- 23 tests passing; ruff and mypy strict clean

## Decisions
- **ADR-0002: `create_all` over Alembic, for now.** One writer, no data anyone depends on, schema churning daily through Day 5. Recorded the trigger that flips it: the first second service that reads these tables.
- Tests run against in-memory SQLite via a `get_session` dependency override — hermetic and fast, while production runs Postgres. Same models, same schema build.
- Ruff's B008 is configured off for `fastapi.Depends`/`Query` rather than suppressed per-line: dependency injection in argument defaults is the framework's design, not a bug.
- Money is stored as integer micros, never floats.

## For the video
1. The domain model first — read the `Campaign` table out loud and connect each column to something in the Netflix posting (brand safety, frequency capping, pacing)
2. `/docs` in the browser: the OpenAPI page generated from the Pydantic models, no hand-written spec
3. `POST /campaigns` with a bad payload — show the typed error envelope, then the same shape from a 404
4. `docker compose logs campaign-service` — one line of JSON per request with `latency_ms`. Say why: Level 3's ops agents correlate these without regex
5. Prometheus targets page showing `campaign-service` up
6. ADR-0002 on screen — the point is not "no migrations," it is writing down the trigger that changes the answer

## Tomorrow
- Day 3: ad-decision-service — targeting, frequency capping via Redis, brand-safety filtering, budget pacing
