# BELLWETHER Level 0 / Day 2 — campaign-service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `campaign-service` — the system of record for advertisers, campaigns, budgets, targeting, brand-safety exclusions, and creatives — as a containerized FastAPI service with Postgres persistence, structured JSON logging, Prometheus metrics, OpenAPI docs, and a full happy-path/failure-path test suite.

**Architecture:** FastAPI + SQLAlchemy 2.0 (typed `Mapped[]` ORM) over Postgres. A shared logging package (`substrate/shared/`) is introduced here and reused by every later service. Tests run against in-memory SQLite through a dependency override, so the suite is fast and hermetic while production runs Postgres.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, psycopg 3, Pydantic v2, pydantic-settings, prometheus-client, uvicorn, pytest, httpx.

## Global Constraints

- Python 3.11+; type hints on all functions; `mypy --strict` must pass on `tests` and `substrate`
- Ruff clean (line length 100); conventional commits
- Every endpoint: Pydantic request/response models, a structured log line carrying `service`, `endpoint`, `latency_ms`, and typed error responses (never a bare 500) — per `docs/standards/coding-standards.md`
- Ads-domain naming only: `campaign`, `creative`, `advertiser`, `frequency_cap`, `pacing`, `brand_safety`
- Postgres runs on host port **5433** (in-network `postgres:5432`); Redis on host **6380**
- `uv` is invoked as `python -m uv`
- Definition of done: day tracker in `docs/site/index.html` updated, `docs/devlog/day-02.md` written

---

### Task 1: Shared structured logging package

**Files:**
- Create: `substrate/__init__.py`, `substrate/shared/__init__.py`, `substrate/shared/logging.py`, `tests/substrate/shared/test_logging.py`
- Modify: `pyproject.toml` (add `pythonpath`, extend `testpaths`)

**Interfaces:**
- Produces: `configure_logging(service: str, level: int = logging.INFO) -> None` and `log_context(logger: logging.Logger, message: str, **context: Any) -> None`. Every later service calls `configure_logging` at startup and `log_context` for request logs. Log records serialize to single-line JSON with keys `ts`, `level`, `service`, `logger`, `message`, plus any context fields.

- [ ] **Step 1: Write failing tests** — assert `JsonFormatter` emits parseable JSON containing the service name, that `log_context` merges context keys into the payload, and that exceptions serialize under an `exception` key.

- [ ] **Step 2:** Run `python -m uv run pytest tests/substrate/shared -v` → FAIL (module not found).

- [ ] **Step 3:** Implement `substrate/shared/logging.py` with `JsonFormatter(logging.Formatter)`, `configure_logging`, `log_context`.

- [ ] **Step 4:** Run the tests → PASS.

- [ ] **Step 5:** Commit `feat: shared structured JSON logging for substrate services`.

---

### Task 2: Domain models, schemas, and database session

**Files:**
- Create: `substrate/campaign_service/__init__.py`, `config.py`, `models.py`, `schemas.py`, `db.py`, `tests/substrate/campaign_service/conftest.py`, `tests/substrate/campaign_service/test_models.py`
- Modify: `pyproject.toml` (runtime dependencies)

**Interfaces:**
- Produces:
  - `models.Base`, `models.Campaign`, `models.Creative` (SQLAlchemy 2.0 `DeclarativeBase`)
  - `Campaign` columns: `id: uuid.UUID`, `name: str`, `advertiser: str`, `status: str` (`draft|active|paused|completed`), `budget_micros: int`, `daily_budget_micros: int`, `frequency_cap_per_day: int`, `targeting: dict[str, Any]`, `brand_safety_exclusions: list[str]`, `starts_at`/`ends_at`/`created_at: datetime`, `creatives: list[Creative]`
  - `Creative` columns: `id`, `campaign_id`, `name`, `duration_seconds: int`, `asset_url: str`
  - `schemas.Targeting`, `CampaignCreate`, `CampaignUpdate`, `CampaignRead`, `CreativeCreate`, `CreativeRead`, `ErrorResponse`
  - `db.get_session() -> Iterator[Session]` — the FastAPI dependency later overridden in tests
  - `config.Settings` with `database_url`, env prefix `CAMPAIGN_`

- [ ] **Step 1:** Add runtime deps to `pyproject.toml` `[project].dependencies`: `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `sqlalchemy>=2.0`, `psycopg[binary]>=3.2`, `pydantic>=2.8`, `pydantic-settings>=2.4`, `prometheus-client>=0.20`; add `httpx>=0.27` to the dev group. Run `python -m uv sync --group dev`.

- [ ] **Step 2:** Write `conftest.py` providing an in-memory SQLite engine (`StaticPool`, `check_same_thread=False`), a `session` fixture, and a `client` fixture that overrides `get_session`.

- [ ] **Step 3:** Write failing model tests: a campaign persists and round-trips its targeting dict and brand-safety list; deleting a campaign cascades to its creatives.

- [ ] **Step 4:** Run → FAIL. Implement `config.py`, `models.py`, `schemas.py`, `db.py`. Run → PASS.

- [ ] **Step 5:** Commit `feat: campaign-service domain models, schemas, and session factory`.

---

### Task 3: FastAPI application and endpoints

**Files:**
- Create: `substrate/campaign_service/main.py`, `tests/substrate/campaign_service/test_api.py`

**Interfaces:**
- Produces `app: FastAPI` exposing:

| Method | Path | Success | Failure |
|---|---|---|---|
| GET | `/health` | 200 `{"status": "ok", "service": "campaign-service"}` | — |
| POST | `/campaigns` | 201 `CampaignRead` | 422 on invalid budget/date/status |
| GET | `/campaigns` | 200 `list[CampaignRead]`, optional `?status=` filter | 422 on unknown status |
| GET | `/campaigns/{campaign_id}` | 200 `CampaignRead` | 404 `ErrorResponse` |
| PATCH | `/campaigns/{campaign_id}` | 200 `CampaignRead` | 404 `ErrorResponse` |
| DELETE | `/campaigns/{campaign_id}` | 204 | 404 `ErrorResponse` |
| POST | `/campaigns/{campaign_id}/creatives` | 201 `CreativeRead` | 404 `ErrorResponse` |
| GET | `/campaigns/{campaign_id}/creatives` | 200 `list[CreativeRead]` | 404 `ErrorResponse` |
| GET | `/metrics` | 200 Prometheus exposition | — |

- Middleware records `http_requests_total{service,endpoint,method,status}` and `http_request_duration_seconds{service,endpoint}`, and emits one structured log line per request with `latency_ms`.
- `HTTPException` handler renders the typed `ErrorResponse` shape `{"error": {"code": int, "message": str}}`.
- Lifespan handler calls `configure_logging("campaign-service")` and `Base.metadata.create_all(engine)`.

- [ ] **Step 1:** Write failing API tests covering every row above — one happy path and one failure path per endpoint, plus a test asserting `/metrics` exposes `http_requests_total` after a request.

- [ ] **Step 2:** Run → FAIL. Implement `main.py`. Run → PASS.

- [ ] **Step 3:** Run `python -m uv run ruff check . && python -m uv run mypy tests substrate` → clean.

- [ ] **Step 4:** Commit `feat: campaign-service API with metrics, structured logs, and typed errors`.

---

### Task 4: Containerize and wire into the stack

**Files:**
- Create: `substrate/campaign_service/Dockerfile`, `.dockerignore`
- Modify: `docker-compose.yml`, `infra/prometheus/prometheus.yml`, `.github/workflows/ci.yml` (mypy target), `README.md` (quickstart)

**Interfaces:**
- Produces: `campaign-service` container on the `bellwether` network, host port **8001**, scraped by Prometheus at `campaign-service:8000/metrics`, depending on `postgres` being healthy.

- [ ] **Step 1:** Write the Dockerfile (python:3.11-slim, `uv sync --frozen --no-dev`, source copied, uvicorn CMD) and `.dockerignore`.

- [ ] **Step 2:** Add the `campaign-service` service to `docker-compose.yml` with `CAMPAIGN_DATABASE_URL=postgresql+psycopg://bellwether:bellwether@postgres:5432/bellwether`, and register the Prometheus scrape job.

- [ ] **Step 3:** Run `docker compose up -d --build campaign-service`; verify `GET /health` returns 200, a `POST /campaigns` round-trips, `/docs` serves OpenAPI, and the Prometheus target is `up`.

- [ ] **Step 4:** Commit `feat: containerize campaign-service and register Prometheus scrape target`.

---

### Task 5: Documentation and definition of done

**Files:**
- Create: `docs/adr/0002-create-all-over-alembic.md`, `docs/devlog/day-02.md`
- Modify: `docs/site/index.html` (day tracker row 2 → shipped, pod segment 2 → shipped, new Level 0 data-model diagram)

- [ ] **Step 1:** Write ADR-0002 — schema managed by `Base.metadata.create_all` for now; Alembic deferred until a second consumer depends on the schema or data must survive a breaking change. Record the trigger that flips the decision.

- [ ] **Step 2:** Add a Mermaid ER diagram of `Campaign`/`Creative` to the Level 0 section of the running doc, mark Day 2 shipped in both the pod strip and the tracker table, and update the header count.

- [ ] **Step 3:** Write `docs/devlog/day-02.md` following the Day 1 format, including the "For the video" shot list.

- [ ] **Step 4:** Full verification: `python -m uv run pytest`, `ruff check`, `mypy tests substrate`, `docker compose ps`.

- [ ] **Step 5:** Commit `docs: ADR-0002, day-02 devlog, running doc updated for campaign-service`.
