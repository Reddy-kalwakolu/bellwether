# BELLWETHER Level 0 / Day 4 — event-service + Grafana dashboards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `event-service` — durable, idempotent ingestion of impression and click events, on-read delivery aggregation (impressions, clicks, CTR, spend) and Prometheus metrics — then put Grafana dashboards over the metrics campaign-service, ad-decision-service, and event-service now emit.

**Architecture:** A FastAPI service over its own Postgres table. Three seams. (1) `models.AdEvent` is append-only and keyed on a **client-supplied `event_id`**, so idempotency is enforced by the primary key rather than a read-then-write check — a retry from Day 5's traffic-simulator can never double-count an impression. (2) `aggregation.py` holds the delivery rollup as a single `GROUP BY` executed on read; there is no rollup table yet (ADR-0004 records the trigger that adds one). (3) Dashboards are **provisioned as code** from `infra/grafana/` — checked-in JSON, validated by a hermetic test, so a fresh `docker compose up` lands on working graphs with no click-ops.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2, psycopg 3, prometheus-client, pytest (in-memory SQLite), Grafana 11 file provisioning, Prometheus.

## Global Constraints

- Python 3.11+; type hints on all functions; `mypy --strict` must pass on `tests` and `substrate`
- Ruff clean (line length 100); `ruff format --check` clean; conventional commits
- Every endpoint: Pydantic request/response models, a structured log line carrying `service`, `endpoint`, `latency_ms`, and typed error responses (never a bare 500) — per `docs/standards/coding-standards.md`
- Request metrics and the request log line come from `substrate/shared/observability.py` via `install_request_observability(app, service_name, logger)`. **Never redefine `http_requests_total` or `http_request_duration_seconds`** — Prometheus keeps one registry per process and the test suite imports every service at once
- Ads-domain naming only: `impression`, `click`, `delivery`, `campaign`, `creative`, `member`, `slot`, `spend`, `ctr`
- **Tests must be hermetic**: the whole suite passes with Docker stopped. No test may touch a real Postgres, Redis, Prometheus, or Grafana
- Host ports: Postgres **5433**, Redis **6380**, campaign-service **8001**, ad-decision-service **8002**, event-service **8003**, Prometheus **9090**, Grafana **3000**
- Every directory under `tests/` needs an `__init__.py` — duplicate test module basenames collide in both pytest and mypy otherwise
- `uv` is invoked as `python -m uv`
- Definition of done: `docs/site/index.html` day tracker updated, `docs/devlog/day-04.md` written

## File Structure

| File | Responsibility |
|---|---|
| `substrate/event_service/config.py` | `EVENT_*` settings — database URL, service name |
| `substrate/event_service/models.py` | `AdEvent` — the append-only event table, its own `Base` |
| `substrate/event_service/db.py` | Engine, session factory, `get_session` dependency |
| `substrate/event_service/schemas.py` | `AdEventCreate`, `AdEventRead`, `EventAck`, `CampaignDelivery`, error envelope |
| `substrate/event_service/aggregation.py` | The delivery `GROUP BY` and the CTR calculation — the only module that knows how a rollup is computed |
| `substrate/event_service/main.py` | HTTP API, idempotent ingest, metrics, typed errors |
| `substrate/event_service/Dockerfile` | Container image |
| `infra/grafana/provisioning/dashboards/dashboards.yml` | Grafana file-provisioning provider |
| `infra/grafana/provisioning/dashboards/substrate-health.json` | Dashboard 1 — traffic, errors, latency, target health |
| `infra/grafana/provisioning/dashboards/ads-delivery.json` | Dashboard 2 — fill rate, no-fill reasons, events, CTR, spend |
| `tests/infra/test_grafana_dashboards.py` | Validates the checked-in dashboard JSON without running Grafana |

---

### Task 1: The event store — config, models, schemas, session

**Files:**
- Create: `substrate/event_service/__init__.py`, `config.py`, `models.py`, `db.py`, `schemas.py`
- Create: `tests/substrate/event_service/__init__.py`, `tests/substrate/event_service/conftest.py`, `tests/substrate/event_service/test_models.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `config.Settings` (env prefix `EVENT_`) with `service_name: str = "event-service"`, `database_url: str`; module-level `settings`
  - `models.Base`, `models.AdEvent` (table `ad_events`, primary key `event_id: uuid.UUID`)
  - `db.engine`, `db.SessionFactory`, `db.get_session() -> Iterator[Session]`
  - `schemas.EventType` (`Literal["impression", "click"]`), `schemas.AdEventCreate`, `schemas.AdEventRead`, `schemas.EventAck`, `schemas.CampaignDelivery`, `schemas.ErrorDetail`, `schemas.ErrorResponse`

- [ ] **Step 1: Write the failing model tests**

Create `tests/substrate/event_service/__init__.py` (empty file).

Create `tests/substrate/event_service/conftest.py`:

```python
"""Fixtures backing event-service tests with hermetic in-memory SQLite."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from substrate.event_service.models import Base

CAMPAIGN_ID = "11111111-1111-1111-1111-111111111111"
CREATIVE_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session() -> Iterator[Session]:
    """A session bound to a fresh in-memory database per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def impression_payload() -> dict[str, Any]:
    """One served impression, as the traffic-simulator will report it."""
    return {
        "event_id": str(uuid4()),
        "event_type": "impression",
        "request_id": str(uuid4()),
        "campaign_id": CAMPAIGN_ID,
        "creative_id": CREATIVE_ID,
        "member_id": "member-1",
        "slot_id": "slot-1",
        "price_micros": 2_000,
        "occurred_at": datetime.now(UTC).isoformat(),
    }
```

Create `tests/substrate/event_service/test_models.py`:

```python
"""The event table is append-only and keyed on the caller's event id."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from substrate.event_service.models import AdEvent
from substrate.event_service.schemas import AdEventCreate


def build(payload: dict[str, Any]) -> AdEvent:
    """Turn a validated create payload into a row."""
    return AdEvent(**AdEventCreate.model_validate(payload).model_dump())


def test_an_impression_round_trips(session: Session, impression_payload: dict[str, Any]) -> None:
    session.add(build(impression_payload))
    session.commit()

    stored = session.get(AdEvent, UUID(impression_payload["event_id"]))
    assert stored is not None
    assert stored.event_type == "impression"
    assert stored.price_micros == 2_000
    assert stored.member_id == "member-1"


def test_the_same_event_id_cannot_be_stored_twice(
    session: Session, impression_payload: dict[str, Any]
) -> None:
    session.add(build(impression_payload))
    session.commit()
    session.add(build(impression_payload))
    with pytest.raises(IntegrityError):
        session.commit()


def test_occurred_at_defaults_to_now_when_the_caller_omits_it(
    impression_payload: dict[str, Any],
) -> None:
    del impression_payload["occurred_at"]
    event = AdEventCreate.model_validate(impression_payload)
    assert (datetime.now(UTC) - event.occurred_at).total_seconds() < 5


def test_an_unknown_event_type_is_rejected(impression_payload: dict[str, Any]) -> None:
    impression_payload["event_type"] = "purchase"
    with pytest.raises(ValueError):
        AdEventCreate.model_validate(impression_payload)


def test_a_click_carries_no_spend(impression_payload: dict[str, Any]) -> None:
    impression_payload["event_type"] = "click"
    impression_payload["event_id"] = str(uuid4())
    del impression_payload["price_micros"]
    event = AdEventCreate.model_validate(impression_payload)
    assert event.price_micros == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest tests/substrate/event_service -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'substrate.event_service'`

- [ ] **Step 3: Implement config, models, db, and schemas**

Create `substrate/event_service/__init__.py` (empty file).

Create `substrate/event_service/config.py`:

```python
"""Runtime configuration for event-service."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings read from `EVENT_*` environment variables.

    The default points at the host-published Postgres port so the service can be
    run outside Docker; inside the compose network the URL is supplied explicitly.
    """

    model_config = SettingsConfigDict(env_prefix="EVENT_")

    database_url: str = "postgresql+psycopg://bellwether:bellwether@localhost:5433/bellwether"
    service_name: str = "event-service"


settings = Settings()
```

Create `substrate/event_service/models.py`:

```python
"""The event table: every impression and click the platform served.

Append-only on purpose. The primary key is the *caller's* event id, which is what
makes ingestion idempotent — a retried delivery report collides with itself instead
of inflating the numbers (ADR-0004).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every event-service table."""


class AdEvent(Base):
    """One impression or click, tied back to the decision that produced it."""

    __tablename__ = "ad_events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(16), index=True)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    creative_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    member_id: Mapped[str] = mapped_column(String(64), index=True)
    slot_id: Mapped[str] = mapped_column(String(64))
    # Clicks carry no spend; only an impression costs the advertiser anything.
    price_micros: Mapped[int] = mapped_column(Integer, default=0)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

Create `substrate/event_service/db.py`:

```python
"""Database engine and session dependency for event-service."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from substrate.event_service.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Yield a request-scoped session; tests override this dependency."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
```

Create `substrate/event_service/schemas.py`:

```python
"""Request and response models for the event-service API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal["impression", "click"]


def _now() -> datetime:
    """Ingestion timestamp for callers that do not supply one."""
    return datetime.now(UTC)


class AdEventCreate(BaseModel):
    """A delivery report: one impression or click that actually happened.

    `event_id` is supplied by the caller and is the idempotency key. A simulator
    (or a real SDK) that retries a report sends the same id and is deduplicated.
    """

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: EventType
    request_id: uuid.UUID
    campaign_id: uuid.UUID
    creative_id: uuid.UUID
    member_id: str = Field(min_length=1, max_length=64)
    slot_id: str = Field(min_length=1, max_length=64)
    price_micros: int = Field(default=0, ge=0)
    occurred_at: datetime = Field(default_factory=_now)


class AdEventRead(BaseModel):
    """An event as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    event_id: uuid.UUID
    event_type: EventType
    request_id: uuid.UUID
    campaign_id: uuid.UUID
    creative_id: uuid.UUID
    member_id: str
    slot_id: str
    price_micros: int
    occurred_at: datetime
    recorded_at: datetime | None = None


class EventAck(BaseModel):
    """The answer to an ingest: stored, or already known."""

    event_id: uuid.UUID
    status: Literal["recorded", "duplicate"]


class CampaignDelivery(BaseModel):
    """What a campaign actually delivered, aggregated from its events."""

    campaign_id: uuid.UUID
    impressions: int
    clicks: int
    click_through_rate: float
    spend_micros: int


class ErrorDetail(BaseModel):
    """The body of a failed request."""

    code: int
    message: str


class ErrorResponse(BaseModel):
    """Every non-2xx response uses this shape."""

    error: ErrorDetail
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m uv run pytest tests/substrate/event_service -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add substrate/event_service tests/substrate/event_service
git commit -m "feat: event-service config, append-only event model, and API schemas"
```

---

### Task 2: Delivery aggregation

**Files:**
- Create: `substrate/event_service/aggregation.py`, `tests/substrate/event_service/test_aggregation.py`

**Interfaces:**
- Consumes: `models.AdEvent`, `schemas.CampaignDelivery`
- Produces:
  - `aggregation.IMPRESSION: str`, `aggregation.CLICK: str`
  - `aggregation.click_through_rate(impressions: int, clicks: int) -> float`
  - `aggregation.day_bounds(day: date) -> tuple[datetime, datetime]`
  - `aggregation.delivery_for_campaign(session: Session, campaign_id: uuid.UUID, day: date | None) -> CampaignDelivery`
  - `aggregation.delivery_rollup(session: Session, day: date | None) -> list[CampaignDelivery]`

Aggregation runs on read: one `GROUP BY campaign_id` over `ad_events`, no rollup table. Spend counts **impressions only** — a click costs the advertiser nothing in this model. A campaign with no events at all still answers with an all-zero `CampaignDelivery` rather than a 404: "delivered nothing" is a real, useful answer for a campaign that just launched.

- [ ] **Step 1: Write the failing aggregation tests**

Create `tests/substrate/event_service/test_aggregation.py`:

```python
"""Delivery rollups: impressions, clicks, CTR, and spend, computed on read."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from substrate.event_service.aggregation import (
    click_through_rate,
    delivery_for_campaign,
    delivery_rollup,
)
from substrate.event_service.models import AdEvent

CAMPAIGN_A = UUID("11111111-1111-1111-1111-111111111111")
CAMPAIGN_B = UUID("33333333-3333-3333-3333-333333333333")
DAY = date(2026, 7, 21)
NOON = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def record(session: Session, **overrides: Any) -> None:
    """Insert one event, defaulting every field the test does not care about."""
    fields: dict[str, Any] = {
        "event_id": uuid4(),
        "event_type": "impression",
        "request_id": uuid4(),
        "campaign_id": CAMPAIGN_A,
        "creative_id": uuid4(),
        "member_id": "member-1",
        "slot_id": "slot-1",
        "price_micros": 2_000,
        "occurred_at": NOON,
    }
    fields.update(overrides)
    session.add(AdEvent(**fields))
    session.commit()


def test_click_through_rate_is_zero_when_nothing_was_served() -> None:
    assert click_through_rate(0, 0) == 0.0
    assert click_through_rate(0, 5) == 0.0


def test_click_through_rate_is_clicks_over_impressions() -> None:
    assert click_through_rate(4, 1) == 0.25


def test_a_campaign_with_no_events_delivers_zeroes(session: Session) -> None:
    delivery = delivery_for_campaign(session, CAMPAIGN_A, day=None)
    assert delivery.impressions == 0
    assert delivery.clicks == 0
    assert delivery.spend_micros == 0
    assert delivery.click_through_rate == 0.0


def test_delivery_counts_impressions_clicks_and_impression_spend(session: Session) -> None:
    for _ in range(4):
        record(session)
    record(session, event_type="click", price_micros=0)

    delivery = delivery_for_campaign(session, CAMPAIGN_A, day=None)
    assert delivery.impressions == 4
    assert delivery.clicks == 1
    assert delivery.spend_micros == 8_000
    assert delivery.click_through_rate == 0.25


def test_delivery_is_scoped_to_one_campaign(session: Session) -> None:
    record(session)
    record(session, campaign_id=CAMPAIGN_B)
    record(session, campaign_id=CAMPAIGN_B)

    assert delivery_for_campaign(session, CAMPAIGN_A, day=None).impressions == 1
    assert delivery_for_campaign(session, CAMPAIGN_B, day=None).impressions == 2


def test_a_day_filter_excludes_yesterdays_events(session: Session) -> None:
    record(session)
    record(session, occurred_at=NOON - timedelta(days=1))

    assert delivery_for_campaign(session, CAMPAIGN_A, day=DAY).impressions == 1
    assert delivery_for_campaign(session, CAMPAIGN_A, day=None).impressions == 2


def test_the_rollup_returns_one_row_per_campaign_in_id_order(session: Session) -> None:
    record(session, campaign_id=CAMPAIGN_B)
    record(session)
    record(session, event_type="click", price_micros=0)

    rollup = delivery_rollup(session, day=None)
    assert [row.campaign_id for row in rollup] == [CAMPAIGN_A, CAMPAIGN_B]
    assert rollup[0].impressions == 1
    assert rollup[0].clicks == 1
    assert rollup[1].spend_micros == 2_000


def test_the_rollup_is_empty_before_anything_is_served(session: Session) -> None:
    assert delivery_rollup(session, day=None) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest tests/substrate/event_service/test_aggregation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'substrate.event_service.aggregation'`

- [ ] **Step 3: Implement the aggregation**

Create `substrate/event_service/aggregation.py`:

```python
"""Delivery rollups, computed on read.

One `GROUP BY campaign_id` over the event table answers every delivery question
the substrate currently asks. There is no materialized rollup table: at Level 0
traffic volumes the query is trivial, and a rollup would be a second copy of the
truth to keep in sync. ADR-0004 records the trigger that adds one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from substrate.event_service.models import AdEvent
from substrate.event_service.schemas import CampaignDelivery

IMPRESSION = "impression"
CLICK = "click"


def click_through_rate(impressions: int, clicks: int) -> float:
    """Clicks per impression, and zero rather than an error when nothing served."""
    if impressions <= 0:
        return 0.0
    return round(clicks / impressions, 6)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC half-open interval `[start, end)` covering `day`."""
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _counters() -> tuple[object, object, object]:
    """The three aggregate expressions every delivery query selects."""
    impressions = func.sum(case((AdEvent.event_type == IMPRESSION, 1), else_=0))
    clicks = func.sum(case((AdEvent.event_type == CLICK, 1), else_=0))
    spend = func.sum(case((AdEvent.event_type == IMPRESSION, AdEvent.price_micros), else_=0))
    return impressions, clicks, spend


def _scoped(query: Select[tuple[object, ...]], day: date | None) -> Select[tuple[object, ...]]:
    """Narrow a delivery query to a single UTC day, when one was asked for."""
    if day is None:
        return query
    start, end = day_bounds(day)
    return query.where(AdEvent.occurred_at >= start, AdEvent.occurred_at < end)


def _delivery(campaign_id: uuid.UUID, impressions: int, clicks: int, spend: int) -> CampaignDelivery:
    """Assemble one delivery row, deriving CTR from the counts."""
    return CampaignDelivery(
        campaign_id=campaign_id,
        impressions=impressions,
        clicks=clicks,
        click_through_rate=click_through_rate(impressions, clicks),
        spend_micros=spend,
    )


def delivery_for_campaign(
    session: Session, campaign_id: uuid.UUID, day: date | None
) -> CampaignDelivery:
    """What one campaign delivered — all zeroes if it has served nothing yet."""
    impressions, clicks, spend = _counters()
    query = _scoped(
        select(impressions, clicks, spend).where(AdEvent.campaign_id == campaign_id), day
    )
    row = session.execute(query).one()
    return _delivery(campaign_id, int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))


def delivery_rollup(session: Session, day: date | None) -> list[CampaignDelivery]:
    """One delivery row per campaign that has served at least one event."""
    impressions, clicks, spend = _counters()
    query = _scoped(select(AdEvent.campaign_id, impressions, clicks, spend), day)
    query = query.group_by(AdEvent.campaign_id).order_by(AdEvent.campaign_id)
    return [
        _delivery(row[0], int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
        for row in session.execute(query).all()
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m uv run pytest tests/substrate/event_service/test_aggregation.py -v`
Expected: PASS (8 tests)

If mypy complains about the `Select[tuple[object, ...]]` annotations on `_scoped`, widen the
signature to `Select[Any]` with `from typing import Any` rather than loosening `strict`.

- [ ] **Step 5: Commit**

```bash
git add substrate/event_service/aggregation.py tests/substrate/event_service/test_aggregation.py
git commit -m "feat: delivery aggregation — impressions, clicks, CTR, and spend on read"
```

---

### Task 3: The FastAPI application

**Files:**
- Create: `substrate/event_service/main.py`, `tests/substrate/event_service/test_api.py`
- Modify: `tests/substrate/event_service/conftest.py` (add the `client` fixture)

**Interfaces:**
- Consumes: everything from Tasks 1–2
- Produces `app: FastAPI` exposing:

| Method | Path | Success | Failure |
|---|---|---|---|
| GET | `/health` | 200 `{"status": "ok", "service": "event-service"}` | — |
| POST | `/events` | 201 `EventAck{status:"recorded"}`; 200 `EventAck{status:"duplicate"}` on a replay | 422 `ErrorResponse` on an invalid body |
| GET | `/events` | 200 `list[AdEventRead]`, newest first, `?campaign_id=`, `?member_id=`, `?limit=` (1–500, default 50) | 422 `ErrorResponse` |
| GET | `/campaigns/{campaign_id}/delivery` | 200 `CampaignDelivery`, `?day=YYYY-MM-DD` | 422 `ErrorResponse` |
| GET | `/delivery` | 200 `list[CampaignDelivery]`, `?day=YYYY-MM-DD` | 422 `ErrorResponse` |
| GET | `/metrics` | 200 Prometheus exposition | — |

- Metrics: the shared request pair via `install_request_observability`, plus `ad_events_total{service,event_type}`, `ad_events_duplicate_total{service}`, and `ad_spend_micros_total{service}`.
- Every ingest emits one structured log line with `service`, `endpoint`, `event_id`, `event_type`, `campaign_id`, `member_id`, `price_micros`, `status`.

- [ ] **Step 1: Add the client fixture**

Append to `tests/substrate/event_service/conftest.py`:

```python


@pytest.fixture
def client(session: Session) -> Iterator[Any]:
    """A TestClient whose requests run against the in-memory session.

    Built without entering its context manager on purpose: the lifespan provisions
    the schema on the real Postgres engine, and this suite needs no infrastructure.
    """
    from fastapi.testclient import TestClient

    from substrate.event_service.db import get_session
    from substrate.event_service.main import app

    def override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Write the failing API tests**

Create `tests/substrate/event_service/test_api.py`:

```python
"""Endpoint contracts: idempotent ingest, delivery rollups, metrics, typed errors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from tests.substrate.event_service.conftest import CAMPAIGN_ID


def other(payload: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """A distinct event derived from `payload`."""
    body = dict(payload)
    body["event_id"] = str(uuid4())
    body.update(overrides)
    return body


def test_health_reports_the_service_name(client: Any) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "event-service"}


def test_an_impression_is_recorded(client: Any, impression_payload: dict[str, Any]) -> None:
    response = client.post("/events", json=impression_payload)
    assert response.status_code == 201
    assert response.json() == {
        "event_id": impression_payload["event_id"],
        "status": "recorded",
    }


def test_replaying_an_event_id_is_a_duplicate_not_a_second_impression(
    client: Any, impression_payload: dict[str, Any]
) -> None:
    assert client.post("/events", json=impression_payload).status_code == 201

    replay = client.post("/events", json=impression_payload)
    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate"

    delivery = client.get(f"/campaigns/{CAMPAIGN_ID}/delivery").json()
    assert delivery["impressions"] == 1
    assert delivery["spend_micros"] == 2_000


def test_delivery_reports_impressions_clicks_ctr_and_spend(
    client: Any, impression_payload: dict[str, Any]
) -> None:
    for _ in range(4):
        client.post("/events", json=other(impression_payload))
    client.post("/events", json=other(impression_payload, event_type="click", price_micros=0))

    delivery = client.get(f"/campaigns/{CAMPAIGN_ID}/delivery").json()
    assert delivery["impressions"] == 4
    assert delivery["clicks"] == 1
    assert delivery["click_through_rate"] == 0.25
    assert delivery["spend_micros"] == 8_000


def test_delivery_for_an_unknown_campaign_is_zeroes_not_a_404(client: Any) -> None:
    response = client.get(f"/campaigns/{uuid4()}/delivery")
    assert response.status_code == 200
    assert response.json()["impressions"] == 0


def test_the_rollup_lists_every_campaign_that_served(
    client: Any, impression_payload: dict[str, Any]
) -> None:
    client.post("/events", json=impression_payload)
    client.post(
        "/events",
        json=other(impression_payload, campaign_id="33333333-3333-3333-3333-333333333333"),
    )

    rollup = client.get("/delivery").json()
    assert len(rollup) == 2
    assert {row["campaign_id"] for row in rollup} == {
        CAMPAIGN_ID,
        "33333333-3333-3333-3333-333333333333",
    }


def test_a_day_filter_narrows_the_rollup(
    client: Any, impression_payload: dict[str, Any]
) -> None:
    yesterday = datetime.now(UTC) - timedelta(days=1)
    client.post("/events", json=impression_payload)
    client.post("/events", json=other(impression_payload, occurred_at=yesterday.isoformat()))

    today = datetime.now(UTC).date().isoformat()
    rollup = client.get("/delivery", params={"day": today}).json()
    assert rollup[0]["impressions"] == 1


def test_recent_events_are_listed_newest_first(
    client: Any, impression_payload: dict[str, Any]
) -> None:
    older = datetime.now(UTC) - timedelta(hours=1)
    client.post("/events", json=other(impression_payload, occurred_at=older.isoformat()))
    client.post("/events", json=impression_payload)

    events = client.get("/events", params={"limit": 10}).json()
    assert [event["event_id"] for event in events][0] == impression_payload["event_id"]
    assert len(events) == 2


def test_listing_events_can_be_scoped_to_one_campaign(
    client: Any, impression_payload: dict[str, Any]
) -> None:
    client.post("/events", json=impression_payload)
    client.post(
        "/events",
        json=other(impression_payload, campaign_id="33333333-3333-3333-3333-333333333333"),
    )

    events = client.get("/events", params={"campaign_id": CAMPAIGN_ID}).json()
    assert len(events) == 1
    assert events[0]["campaign_id"] == CAMPAIGN_ID


def test_an_invalid_event_returns_the_typed_error_envelope(
    client: Any, impression_payload: dict[str, Any]
) -> None:
    impression_payload["event_type"] = "purchase"
    response = client.post("/events", json=impression_payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == 422


def test_metrics_expose_the_event_counters(
    client: Any, impression_payload: dict[str, Any]
) -> None:
    client.post("/events", json=impression_payload)
    client.post("/events", json=impression_payload)  # duplicate

    body = client.get("/metrics").text
    assert "ad_events_total" in body
    assert 'event_type="impression"' in body
    assert "ad_events_duplicate_total" in body
    assert "ad_spend_micros_total" in body
    assert "http_requests_total" in body
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest tests/substrate/event_service/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'substrate.event_service.main'`

- [ ] **Step 4: Implement the application**

Create `substrate/event_service/main.py`:

```python
"""event-service HTTP API.

Where the serving path becomes a durable record. Impressions and clicks land here,
are deduplicated by the caller's event id, and are aggregated on read into the
delivery numbers — impressions, clicks, CTR, spend — that the Grafana dashboards
and, three levels from now, the RCA agent both read.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import date
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from substrate.event_service.aggregation import delivery_for_campaign, delivery_rollup
from substrate.event_service.config import settings
from substrate.event_service.db import engine, get_session
from substrate.event_service.models import AdEvent, Base
from substrate.event_service.schemas import (
    AdEventCreate,
    AdEventRead,
    CampaignDelivery,
    ErrorResponse,
    EventAck,
)
from substrate.shared.logging import configure_logging, log_context
from substrate.shared.observability import install_request_observability

logger = logging.getLogger("event_service.api")

EVENTS = Counter(
    "ad_events_total",
    "Delivery events ingested, by type.",
    ["service", "event_type"],
)
DUPLICATES = Counter(
    "ad_events_duplicate_total",
    "Delivery events rejected because their event id was already stored.",
    ["service"],
)
SPEND = Counter(
    "ad_spend_micros_total",
    "Advertiser spend recorded from impressions, in micros.",
    ["service"],
)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid request"}
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure logging and ensure the schema exists before serving traffic."""
    configure_logging(settings.service_name)
    Base.metadata.create_all(engine)
    logger.info("event-service ready")
    yield


app = FastAPI(
    title="event-service",
    version="0.1.0",
    summary="Idempotent impression and click ingestion, and delivery aggregation.",
    lifespan=lifespan,
)


install_request_observability(app, settings.service_name, logger)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    """Render HTTP errors in the typed ErrorResponse shape."""
    body = ErrorResponse.model_validate({"error": {"code": exc.status_code, "message": exc.detail}})
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Render validation failures in the same typed shape as other errors."""
    body = ErrorResponse.model_validate({"error": {"code": 422, "message": str(exc.errors())}})
    return JSONResponse(status_code=422, content=body.model_dump())


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness probe used by Docker Compose and the deployment-validation agent."""
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics", tags=["ops"], include_in_schema=False)
def metrics() -> Response:
    """Prometheus exposition for this service."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post(
    "/events",
    response_model=EventAck,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def ingest_event(
    payload: AdEventCreate,
    response: Response,
    session: Session = Depends(get_session),
) -> EventAck:
    """Record one impression or click. Replaying an event id is a no-op, not a double count.

    Deduplication is the primary key, not a read-then-write check: two concurrent
    reports of the same impression cannot both survive the insert.
    """
    session.add(AdEvent(**payload.model_dump()))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        DUPLICATES.labels(settings.service_name).inc()
        response.status_code = status.HTTP_200_OK
        _log_ingest(payload, "duplicate")
        return EventAck(event_id=payload.event_id, status="duplicate")

    EVENTS.labels(settings.service_name, payload.event_type).inc()
    if payload.event_type == "impression":
        SPEND.labels(settings.service_name).inc(payload.price_micros)
    _log_ingest(payload, "recorded")
    return EventAck(event_id=payload.event_id, status="recorded")


def _log_ingest(payload: AdEventCreate, outcome: str) -> None:
    """One structured line per delivery report, whatever became of it."""
    log_context(
        logger,
        "event ingested",
        service=settings.service_name,
        endpoint="/events",
        event_id=str(payload.event_id),
        event_type=payload.event_type,
        campaign_id=str(payload.campaign_id),
        member_id=payload.member_id,
        price_micros=payload.price_micros,
        status=outcome,
    )


@app.get("/events", response_model=list[AdEventRead], responses=ERROR_RESPONSES)
def list_events(
    session: Session = Depends(get_session),
    campaign_id: uuid.UUID | None = Query(default=None),
    member_id: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[AdEvent]:
    """The most recent delivery reports, newest first — the raw tape behind the rollups."""
    query = select(AdEvent).order_by(AdEvent.occurred_at.desc()).limit(limit)
    if campaign_id is not None:
        query = query.where(AdEvent.campaign_id == campaign_id)
    if member_id is not None:
        query = query.where(AdEvent.member_id == member_id)
    return list(session.scalars(query).all())


@app.get("/campaigns/{campaign_id}/delivery", response_model=CampaignDelivery)
def campaign_delivery(
    campaign_id: uuid.UUID,
    session: Session = Depends(get_session),
    day: date | None = Query(default=None),
) -> CampaignDelivery:
    """What one campaign delivered — all zeroes if it has served nothing yet."""
    return delivery_for_campaign(session, campaign_id, day)


@app.get("/delivery", response_model=list[CampaignDelivery])
def delivery(
    session: Session = Depends(get_session),
    day: date | None = Query(default=None),
) -> list[CampaignDelivery]:
    """One delivery row per campaign that has served at least one event."""
    return delivery_rollup(session, day)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m uv run pytest tests/substrate/event_service -v`
Expected: PASS (all 12 API tests plus the 13 from Tasks 1–2)

- [ ] **Step 6: Verify the whole suite is still hermetic and clean**

Run: `python -m uv run pytest && python -m uv run ruff check . && python -m uv run ruff format --check . && python -m uv run mypy tests substrate`
Expected: all green, with Docker stopped.

- [ ] **Step 7: Commit**

```bash
git add substrate/event_service/main.py tests/substrate/event_service
git commit -m "feat: event-service API with idempotent ingestion and delivery rollups"
```

---

### Task 4: Containerize and wire into the stack

**Files:**
- Create: `substrate/event_service/Dockerfile`
- Modify: `docker-compose.yml`, `infra/prometheus/prometheus.yml`, `README.md`

**Interfaces:**
- Produces: an `event-service` container on the `bellwether` network, host port **8003**, scraped by Prometheus at `event-service:8000/metrics`, depending on a healthy Postgres.

- [ ] **Step 1: Write the Dockerfile**

Create `substrate/event_service/Dockerfile`:

```dockerfile
# event-service: durable impression and click ingestion, delivery aggregation.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Dependencies first so image layers cache across source changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY substrate ./substrate

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "substrate.event_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Add the service to Compose**

In `docker-compose.yml`, after the `ad-decision-service` block:

```yaml
  event-service:
    build:
      context: .
      dockerfile: substrate/event_service/Dockerfile
    environment:
      EVENT_DATABASE_URL: postgresql+psycopg://bellwether:bellwether@postgres:5432/bellwether
    ports: ["8003:8000"]
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\""]
      interval: 10s
      timeout: 3s
      retries: 5
```

- [ ] **Step 3: Register the Prometheus scrape target**

In `infra/prometheus/prometheus.yml`, add below the ad-decision-service job:

```yaml
  - job_name: event-service
    static_configs:
      - targets: ["event-service:8000"]
```

Update the trailing comment to `# Remaining substrate services register here as they are built (Day 5+).`

- [ ] **Step 4: Update the README**

In `README.md`: tick `- [x] Day 4 — event-service + observability`, change **Status** to `**Day 4** — Level 0 in progress.`, and add a row to the service table:

```markdown
| event-service API docs | http://localhost:8003/docs |
```

- [ ] **Step 5: Commit**

```bash
git add substrate/event_service/Dockerfile docker-compose.yml infra/prometheus/prometheus.yml README.md
git commit -m "feat: containerize event-service and register its Prometheus scrape target"
```

---

### Task 5: Grafana dashboards as code

**Files:**
- Create: `infra/grafana/provisioning/dashboards/dashboards.yml`
- Create: `infra/grafana/provisioning/dashboards/substrate-health.json`
- Create: `infra/grafana/provisioning/dashboards/ads-delivery.json`
- Create: `tests/infra/__init__.py`, `tests/infra/test_grafana_dashboards.py`

**Interfaces:**
- Consumes: metric names emitted by all three services — `http_requests_total{service,endpoint,method,status}`, `http_request_duration_seconds{service,endpoint}`, `ad_decisions_total{service,outcome}`, `ad_candidates_filtered_total{service,reason}`, `ad_events_total{service,event_type}`, `ad_events_duplicate_total{service}`, `ad_spend_micros_total{service}`
- Produces: two provisioned dashboards, `substrate-health` and `ads-delivery`, loaded from disk at Grafana start. No manual dashboard creation, no exported-JSON-by-hand.

The existing Compose mount (`./infra/grafana/provisioning:/etc/grafana/provisioning:ro`) already covers these files — no Compose change is needed.

> Before writing the panel JSON, invoke the `dataviz` skill: these are charts, and the
> series colors, units, and legend rules it specifies apply to Grafana panels too.

- [ ] **Step 1: Write the failing dashboard validation test**

Create `tests/infra/__init__.py` (empty file).

Create `tests/infra/test_grafana_dashboards.py`:

```python
"""The provisioned Grafana dashboards, validated without running Grafana.

A dashboard that fails to load shows up as an empty Grafana at demo time. These
checks are cheap, hermetic, and catch the mistakes that actually happen: a stray
comma, a panel with no query, a datasource that does not match the provisioned one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

DASHBOARD_DIR = Path("infra/grafana/provisioning/dashboards")
DASHBOARDS = sorted(DASHBOARD_DIR.glob("*.json"))


def load(path: Path) -> dict[str, Any]:
    """Parse one dashboard file."""
    return json.loads(path.read_text(encoding="utf-8"))


def panels(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Every panel in a dashboard, including those nested in rows."""
    found: list[dict[str, Any]] = []
    for panel in dashboard.get("panels", []):
        found.append(panel)
        found.extend(panel.get("panels", []))
    return found


def test_both_dashboards_are_present() -> None:
    assert {path.name for path in DASHBOARDS} == {
        "substrate-health.json",
        "ads-delivery.json",
    }


def test_the_provider_points_at_the_dashboard_directory() -> None:
    provider = yaml.safe_load((DASHBOARD_DIR / "dashboards.yml").read_text(encoding="utf-8"))
    assert provider["apiVersion"] == 1
    assert provider["providers"][0]["type"] == "file"
    assert provider["providers"][0]["options"]["path"] == "/etc/grafana/provisioning/dashboards"


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_a_dashboard_declares_a_title_a_uid_and_panels(path: Path) -> None:
    dashboard = load(path)
    assert dashboard["title"]
    assert dashboard["uid"] == path.stem
    assert panels(dashboard)


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_every_panel_queries_prometheus(path: Path) -> None:
    for panel in panels(load(path)):
        if panel["type"] == "row":
            continue
        assert panel["datasource"]["type"] == "prometheus", panel["title"]
        assert panel["targets"], panel["title"]
        for target in panel["targets"]:
            assert target["expr"].strip(), panel["title"]


def test_dashboard_uids_are_unique() -> None:
    uids = [load(path)["uid"] for path in DASHBOARDS]
    assert len(uids) == len(set(uids))


def test_the_dashboards_only_reference_metrics_the_substrate_emits() -> None:
    known = {
        "up",
        "http_requests_total",
        "http_request_duration_seconds_bucket",
        "http_request_duration_seconds_count",
        "ad_decisions_total",
        "ad_candidates_filtered_total",
        "ad_events_total",
        "ad_events_duplicate_total",
        "ad_spend_micros_total",
    }
    for path in DASHBOARDS:
        for panel in panels(load(path)):
            for target in panel.get("targets", []):
                referenced = {
                    token
                    for token in known
                    if token in target["expr"]
                }
                assert referenced, f"{path.name} / {panel['title']}: {target['expr']}"
```

- [ ] **Step 2: Add PyYAML to the dev group**

The test reads the provider YAML. Edit `pyproject.toml`, adding `"pyyaml>=6.0"` and `"types-pyyaml>=6.0"` to `[dependency-groups].dev`.

Run: `python -m uv sync --group dev`

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m uv run pytest tests/infra -v`
Expected: FAIL — `test_both_dashboards_are_present` fails on an empty set (no dashboards exist yet).

- [ ] **Step 4: Write the provisioning provider**

Create `infra/grafana/provisioning/dashboards/dashboards.yml`:

```yaml
apiVersion: 1
providers:
  - name: bellwether
    type: file
    orgId: 1
    folder: BELLWETHER
    disableDeletion: false
    allowUiUpdates: false
    updateIntervalSeconds: 30
    options:
      path: /etc/grafana/provisioning/dashboards
      foldersFromFilesStructure: false
```

- [ ] **Step 5: Write the substrate-health dashboard**

Create `infra/grafana/provisioning/dashboards/substrate-health.json`. Four panels, all
`datasource: {"type": "prometheus", "uid": "prometheus"}`, `refresh: "10s"`, `time.from: "now-15m"`:

| Panel | Type | Query | Unit |
|---|---|---|---|
| Scrape targets up | `stat` | `sum by (job) (up)` | none, thresholds 0 red / 1 green |
| Request rate by service | `timeseries` | `sum by (service) (rate(http_requests_total[1m]))` | `reqps` |
| Error ratio by service | `timeseries` | `sum by (service) (rate(http_requests_total{status=~"5.."}[5m])) / clamp_min(sum by (service) (rate(http_requests_total[5m])), 0.001)` | `percentunit`, max 1 |
| p95 latency by endpoint | `timeseries` | `histogram_quantile(0.95, sum by (le, service, endpoint) (rate(http_request_duration_seconds_bucket[5m])))` | `s` |

The uid must be `substrate-health` (the test asserts uid == filename stem). Give every
`timeseries` panel `fieldConfig.defaults.custom.lineWidth: 2`, `fillOpacity: 8`, and
`legend.displayMode: "table"` with `calcs: ["lastNotNull"]` so the legend reads as a value
table rather than a color key.

- [ ] **Step 6: Write the ads-delivery dashboard**

Create `infra/grafana/provisioning/dashboards/ads-delivery.json`, uid `ads-delivery`, same
datasource and refresh:

| Panel | Type | Query | Unit |
|---|---|---|---|
| Fill rate | `stat` | `sum(rate(ad_decisions_total{outcome="filled"}[5m])) / clamp_min(sum(rate(ad_decisions_total[5m])), 0.001)` | `percentunit`, thresholds red < 0.5 < yellow < 0.8 < green |
| Decisions by outcome | `timeseries` | `sum by (outcome) (rate(ad_decisions_total[1m]))` | `reqps` |
| Why candidates lost | `timeseries` | `sum by (reason) (rate(ad_candidates_filtered_total{reason!="eligible"}[1m]))` | `reqps`, stacked |
| Events ingested | `timeseries` | `sum by (event_type) (rate(ad_events_total[1m]))` | `reqps` |
| Click-through rate | `stat` | `sum(rate(ad_events_total{event_type="click"}[5m])) / clamp_min(sum(rate(ad_events_total{event_type="impression"}[5m])), 0.001)` | `percentunit` |
| Spend rate | `timeseries` | `sum(rate(ad_spend_micros_total[1m])) / 1000000` | `currencyUSD` |
| Duplicate reports rejected | `stat` | `sum(increase(ad_events_duplicate_total[1h]))` | `short` |

"Why candidates lost" is the diagnostic panel — stacked, one band per rule, so a fill-rate
drop is read off the band that grew. Keep `reason!="eligible"` in the query: the eligible
count dwarfs every rejection and would flatten the panel.

- [ ] **Step 7: Run the test to verify it passes**

Run: `python -m uv run pytest tests/infra -v`
Expected: PASS

- [ ] **Step 8: Verify the dashboards load in a live Grafana**

```bash
docker compose up -d --build
curl -s -X POST localhost:8003/events -H 'content-type: application/json' \
  -d '{"event_type":"impression","request_id":"'"$(python -c 'import uuid;print(uuid.uuid4())')"'",
       "campaign_id":"11111111-1111-1111-1111-111111111111",
       "creative_id":"22222222-2222-2222-2222-222222222222",
       "member_id":"member-1","slot_id":"slot-1","price_micros":2000}'
curl -s localhost:8003/delivery
```

Then open `localhost:3000` → the **BELLWETHER** folder → both dashboards render, and
`localhost:9090/targets` lists `event-service` as up.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock infra/grafana tests/infra
git commit -m "feat: provision Grafana dashboards as code for substrate health and ad delivery"
```

---

### Task 6: Documentation and definition of done

**Files:**
- Create: `docs/adr/0004-aggregate-on-read.md`, `docs/devlog/day-04.md`
- Modify: `docs/site/index.html`, `docs/adr/0003-redis-for-decision-state.md`

- [ ] **Step 1: Write ADR-0004**

Create `docs/adr/0004-aggregate-on-read.md`, following the format of `docs/adr/0003-redis-for-decision-state.md` (Context / Decision / Alternatives considered / Consequences / trigger). Two linked decisions:

1. **Ingestion is idempotent on a caller-supplied `event_id`, enforced by the primary key.** A delivery report is retried whenever a network hiccups; a read-then-write check races. The insert either wins or raises `IntegrityError`, and the second outcome is a `duplicate` ack, not an error.
2. **Delivery is aggregated on read — one `GROUP BY` over `ad_events`, no rollup table.** A rollup is a second copy of the truth that can drift. At Level 0 volumes the query is trivial.

**Record the trigger that flips it:** when Day 5's traffic-simulator sustains load and the `/delivery` p95 crosses ~200 ms, or the events table outgrows a single day's retention, a rollup table (or a Postgres materialized view refreshed on a schedule) becomes worth its cost.

Also note what event-service does *not* change: it writes its own table in the shared Postgres database and reads nobody else's, so ADR-0002's Alembic trigger — "the first time a second service reads these tables" — remains unfired.

- [ ] **Step 2: Resolve ADR-0003's trigger**

ADR-0003 named Day 4 as its flip trigger. Append a short **Update (Day 4)** section to `docs/adr/0003-redis-for-decision-state.md` recording what actually happened: impressions are now durable in event-service, so Postgres holds the auditable record of what was served and what it cost. Redis keeps only the hot serving-path counters, which is what it was good at. The decision stands, narrowed — it did not need to be reversed.

- [ ] **Step 3: Update the running doc**

In `docs/site/index.html`:
1. Top bar: `DAY <b>03</b>` → `DAY <b>04</b>`; footer `DAY 03 / 30` → `DAY 04 / 30`.
2. Pod head: `3 shipped · 27 queued` → `4 shipped · 26 queued`.
3. Pod strip: add `nohead` to the Day 3 segment (`seg shipped nohead`) and change the Day 4 segment to `class="seg shipped"` so the playhead advances.
4. Tracker row `04`: `<td class="st plan">○ QUEUED</td>` → `<td class="st done">● SHIPPED</td>`.
5. In the Level 0 section, after the "Where the decision state lives" diagram, add a "Day 4 — the event path" heading, a sentence on idempotency, and this Mermaid flowchart:

```html
    <h3>Day 4 — the event path</h3>
    <p>Serving an ad is a decision; <em>reporting</em> it is a fact. Events land keyed on an id the caller chose, so a retried report collides with itself instead of inflating the numbers — idempotency enforced by the primary key, not by a check that races.</p>
    <pre class="mermaid">
flowchart LR
    SIM["traffic-simulator&lt;br/&gt;(Day 5)"] -->|"POST /events"| EV["event-service"]
    EV --> PK{"event_id already stored?"}
    PK -->|yes| DUP["200 duplicate&lt;br/&gt;ad_events_duplicate_total"]
    PK -->|no| INS["201 recorded&lt;br/&gt;INSERT into ad_events"]
    INS --> MET["ad_events_total{event_type}&lt;br/&gt;ad_spend_micros_total"]
    INS --> PGE[("Postgres&lt;br/&gt;ad_events — append only")]
    PGE -->|"GROUP BY campaign_id"| DEL["GET /delivery&lt;br/&gt;impressions · clicks · CTR · spend"]
    </pre>
```

6. Add a "Day 4 — what the dashboards read" Mermaid diagram showing all three services' `/metrics` flowing into Prometheus and out to the two provisioned Grafana dashboards:

```html
    <h3>Day 4 — what the dashboards read</h3>
    <pre class="mermaid">
flowchart LR
    CS["campaign-service&lt;br/&gt;/metrics"] --> PROM["Prometheus :9090"]
    ADS["ad-decision-service&lt;br/&gt;/metrics"] --> PROM
    EVS["event-service&lt;br/&gt;/metrics"] --> PROM
    PROM --> D1["Grafana · substrate-health&lt;br/&gt;traffic · errors · p95 · targets"]
    PROM --> D2["Grafana · ads-delivery&lt;br/&gt;fill rate · why candidates lost&lt;br/&gt;events · CTR · spend"]
    </pre>
```

7. Add a decision card to SEQ 04, above the ADR-0003 card:

```html
    <div class="card">
      <h3>ADR-0004 — Idempotent on the primary key, aggregated on read</h3>
      <p>A delivery report gets retried whenever a network hiccups, so the caller chooses the <code>event_id</code> and the primary key does the deduplication — a check-then-insert races, an insert that collides cannot. Delivery numbers are then a single <code>GROUP BY</code> over the event table rather than a rollup that can drift out of sync with it. The trigger that flips this: Day 5's simulator, when sustained load pushes <code>/delivery</code> past ~200&nbsp;ms.</p>
    </div>
```

- [ ] **Step 4: Write the devlog**

Create `docs/devlog/day-04.md` following the Day 3 format exactly — `# Day 4 — event-service + Grafana dashboards`, `**Level:** 0 · **Date:** 2026-07-21`, then `## Shipped`, `## Decisions`, `## For the video`, `## Tomorrow`. The video shot list should cover: posting an impression and getting `201 recorded`; posting the *same* body again and getting `200 duplicate` with the delivery numbers unmoved; a click and the CTR appearing in `/delivery`; `docker compose logs event-service` showing the ingest JSON line; both Grafana dashboards live, with the "why candidates lost" panel called out as the fill-rate diagnosis; and ADR-0004 with the callback to ADR-0003 — Day 4 was the trigger it named, and the decision narrowed rather than reversed. "Tomorrow" points at Day 5 — traffic-simulator with failure injection, and the Level 0 quality gate.

- [ ] **Step 5: Full verification**

Run: `python -m uv run pytest && python -m uv run ruff check . && python -m uv run ruff format --check . && python -m uv run mypy tests substrate`
Then: `docker compose ps` — campaign-service, ad-decision-service, and event-service all healthy.

- [ ] **Step 6: Commit**

```bash
git add docs
git commit -m "docs: ADR-0004, day-04 devlog, running doc updated for event-service and dashboards"
```

---

## Execution notes (what the plan missed)

Recorded during execution, so the next plan does not repeat these:

1. **The provisioned datasource had no pinned uid.** Dashboards reference a datasource by uid; without `uid: prometheus` in `infra/grafana/provisioning/datasources/prometheus.yml`, Grafana generates one at first start and every provisioned panel loads empty. Fixed by pinning it, and by adding a test that asserts the datasource file and the dashboards agree.
2. **`**kwargs: Any` trips ANN401.** Day 3 hit this too and settled on `**overrides: object`; the plan reproduced the `Any` form anyway. Test helpers taking arbitrary overrides use `object`.
3. **Fixtures must be typed concretely, not `Any`.** `client: Any` in test signatures is eleven ANN401 violations. Import `TestClient` at the top of the conftest and annotate the fixture and every test parameter with it — which is what campaign-service already did.
4. **`json.loads` returns `Any`.** Under `mypy --strict`, `return json.loads(...)` from a function declared `-> dict[str, Any]` is a `no-any-return` error. Assign to an annotated local first.
5. **Prometheus and Grafana do not reload provisioning on their own.** After editing `prometheus.yml` or adding dashboards, `docker compose restart prometheus grafana` — `up -d` alone leaves the running containers on the old config, and the new scrape target silently never appears.
6. **Verification pipelines hide failures.** `mypy ... 2>&1 | tail -2` exits with `tail`'s status, so a `&&` chain sails past a real type error into the commit. Run the gates unpiped, or check them separately.
7. **p95 by endpoint was the wrong cut.** Endpoints are open-ended, so the panel would cycle colors through an unbounded series set. Changed to p95 by service — three entities, the same fixed colors as every other panel on the dashboard.
