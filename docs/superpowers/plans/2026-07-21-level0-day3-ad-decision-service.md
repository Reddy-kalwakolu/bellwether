# BELLWETHER Level 0 / Day 3 — ad-decision-service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `ad-decision-service` — the core serving path that turns an ad request (member context + slot) into a selected ad by running eligibility, targeting, brand-safety, frequency-capping, and budget-pacing filters over campaigns fetched from campaign-service.

**Architecture:** FastAPI service with three seams. (1) A `CampaignClient` reads active campaigns from campaign-service over HTTP — never its tables, so ADR-0002's Alembic trigger stays untripped. (2) A `DecisionStore` protocol holds per-member frequency counters and per-campaign daily spend; `RedisDecisionStore` is production, `InMemoryDecisionStore` is the hermetic test double. (3) `decisioning.py` is a pure filter chain over plain dataclasses — no I/O, so every rule is unit-testable and every rejection carries a named reason that the Level 3 ops agents can correlate.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pydantic-settings, redis-py 5, httpx, prometheus-client, pytest.

## Global Constraints

- Python 3.11+; type hints on all functions; `mypy --strict` must pass on `tests` and `substrate`
- Ruff clean (line length 100); `ruff format --check` clean; conventional commits
- Every endpoint: Pydantic request/response models, a structured log line carrying `service`, `endpoint`, `latency_ms`, and typed error responses (never a bare 500) — per `docs/standards/coding-standards.md`
- Ads-domain naming only: `ad_request`, `member`, `slot`, `campaign`, `creative`, `frequency_cap`, `pacing`, `brand_safety`, `no_fill`
- **Tests must be hermetic**: the whole suite passes with Docker stopped. No test may touch a real Redis or a real campaign-service.
- Postgres runs on host port **5433**, Redis on host **6380**, campaign-service on host **8001**, ad-decision-service on host **8002**
- `uv` is invoked as `python -m uv`
- Definition of done: `docs/site/index.html` day tracker updated, `docs/devlog/day-03.md` written

---

### Task 1: Configuration, schemas, and the decision store

**Files:**
- Create: `substrate/ad_decision_service/__init__.py`, `config.py`, `schemas.py`, `store.py`
- Create: `tests/substrate/ad_decision_service/__init__.py` is NOT needed (pytest rootdir uses `pythonpath = ["."]`); create `tests/substrate/ad_decision_service/test_store.py`
- Modify: `pyproject.toml` (move `httpx` to runtime deps, add `redis`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `config.Settings` (env prefix `AD_DECISION_`) with `service_name: str = "ad-decision-service"`, `campaign_service_url: str`, `redis_url: str`, `request_timeout_seconds: float`, `impression_price_micros: int`, `pacing_enabled: bool`; module-level `settings`
  - `schemas.MemberContext`, `schemas.Slot`, `schemas.AdRequest`, `schemas.CandidateTrace`, `schemas.AdDecision`, `schemas.ErrorDetail`, `schemas.ErrorResponse`
  - `store.DecisionStore` (Protocol) with `impression_count(member_id, campaign_id, day) -> int`, `daily_spend_micros(campaign_id, day) -> int`, `record_impression(member_id, campaign_id, day, price_micros) -> None`
  - `store.InMemoryDecisionStore` and `store.RedisDecisionStore`

- [ ] **Step 1: Add dependencies**

Edit `pyproject.toml`: add `"httpx>=0.27"` and `"redis>=5.0"` to `[project].dependencies`, and remove `"httpx>=0.27"` from the `dev` group (it is now a runtime dependency — the service calls campaign-service with it).

Run: `python -m uv sync --group dev`

- [ ] **Step 2: Write the failing store tests**

Create `tests/substrate/ad_decision_service/test_store.py`:

```python
"""The decision store holds per-day frequency counters and pacing spend."""

from __future__ import annotations

from datetime import date

from substrate.ad_decision_service.store import InMemoryDecisionStore, RedisDecisionStore

DAY = date(2026, 7, 21)
MEMBER = "member-1"
CAMPAIGN = "campaign-1"


def test_impression_count_starts_at_zero() -> None:
    store = InMemoryDecisionStore()
    assert store.impression_count(MEMBER, CAMPAIGN, DAY) == 0
    assert store.daily_spend_micros(CAMPAIGN, DAY) == 0


def test_recording_an_impression_advances_frequency_and_spend() -> None:
    store = InMemoryDecisionStore()
    store.record_impression(MEMBER, CAMPAIGN, DAY, price_micros=2_000)
    store.record_impression(MEMBER, CAMPAIGN, DAY, price_micros=2_000)
    assert store.impression_count(MEMBER, CAMPAIGN, DAY) == 2
    assert store.daily_spend_micros(CAMPAIGN, DAY) == 4_000


def test_counters_are_scoped_per_member_and_per_day() -> None:
    store = InMemoryDecisionStore()
    store.record_impression(MEMBER, CAMPAIGN, DAY, price_micros=2_000)
    assert store.impression_count("member-2", CAMPAIGN, DAY) == 0
    assert store.impression_count(MEMBER, CAMPAIGN, date(2026, 7, 22)) == 0
    # spend is per campaign, not per member: a second member adds to the same budget
    store.record_impression("member-2", CAMPAIGN, DAY, price_micros=2_000)
    assert store.daily_spend_micros(CAMPAIGN, DAY) == 4_000


class FakeRedis:
    """Minimal stand-in for the redis-py commands RedisDecisionStore uses."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expiries: list[tuple[str, int]] = []

    def get(self, key: str) -> str | None:
        return None if key not in self.values else str(self.values[key])

    def incrby(self, key: str, amount: int) -> int:
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    def expire(self, key: str, seconds: int) -> None:
        self.expiries.append((key, seconds))


def test_redis_store_uses_day_scoped_keys_and_sets_a_ttl() -> None:
    redis = FakeRedis()
    store = RedisDecisionStore(redis, ttl_seconds=172_800)
    store.record_impression(MEMBER, CAMPAIGN, DAY, price_micros=2_000)

    assert redis.values["freq:member-1:campaign-1:2026-07-21"] == 1
    assert redis.values["spend:campaign-1:2026-07-21"] == 2_000
    assert {key for key, _ in redis.expiries} == {
        "freq:member-1:campaign-1:2026-07-21",
        "spend:campaign-1:2026-07-21",
    }
    assert store.impression_count(MEMBER, CAMPAIGN, DAY) == 1
    assert store.daily_spend_micros(CAMPAIGN, DAY) == 2_000


def test_redis_store_reads_missing_keys_as_zero() -> None:
    store = RedisDecisionStore(FakeRedis(), ttl_seconds=172_800)
    assert store.impression_count(MEMBER, CAMPAIGN, DAY) == 0
    assert store.daily_spend_micros(CAMPAIGN, DAY) == 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest tests/substrate/ad_decision_service -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'substrate.ad_decision_service'`

- [ ] **Step 4: Implement config, schemas, and store**

Create `substrate/ad_decision_service/__init__.py` (empty file).

Create `substrate/ad_decision_service/config.py`:

```python
"""Runtime configuration for ad-decision-service."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings read from `AD_DECISION_*` environment variables.

    Defaults point at host-published ports so the service runs outside Docker;
    inside the compose network the URLs are supplied explicitly.
    """

    model_config = SettingsConfigDict(env_prefix="AD_DECISION_")

    service_name: str = "ad-decision-service"
    campaign_service_url: str = "http://localhost:8001"
    redis_url: str = "redis://localhost:6380/0"
    request_timeout_seconds: float = 2.0
    # Flat price per impression. Real platforms clear an auction; the substrate
    # only needs spend to accumulate believably so pacing has something to pace.
    impression_price_micros: int = 2_000
    pacing_enabled: bool = True


settings = Settings()
```

Create `substrate/ad_decision_service/schemas.py`:

```python
"""Request and response models for the ad-decision-service API."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FilterReason = Literal[
    "eligible",
    "not_active",
    "outside_flight_window",
    "targeting_mismatch",
    "brand_safety_excluded",
    "frequency_capped",
    "pacing_throttled",
    "no_creative",
]


class MemberContext(BaseModel):
    """Who is watching, as far as the decision path is concerned."""

    member_id: str = Field(min_length=1, max_length=64)
    country: str = Field(min_length=2, max_length=2)
    device_type: str = Field(min_length=1, max_length=32)


class Slot(BaseModel):
    """The ad break being filled and the content surrounding it."""

    slot_id: str = Field(min_length=1, max_length=64)
    duration_seconds: int = Field(gt=0, le=180)
    content_rating: str = Field(min_length=1, max_length=16)
    content_categories: list[str] = Field(default_factory=list)


class AdRequest(BaseModel):
    """One opportunity to serve an ad."""

    member: MemberContext
    slot: Slot


class CandidateTrace(BaseModel):
    """Why one campaign won or lost. The decision path's audit trail."""

    campaign_id: uuid.UUID
    campaign_name: str
    reason: FilterReason


class SelectedAd(BaseModel):
    """The ad chosen for a slot."""

    campaign_id: uuid.UUID
    campaign_name: str
    advertiser: str
    creative_id: uuid.UUID
    creative_name: str
    asset_url: str
    duration_seconds: int
    price_micros: int


class AdDecision(BaseModel):
    """The response to an ad request: a fill or an explained no-fill."""

    model_config = ConfigDict(from_attributes=True)

    request_id: uuid.UUID
    slot_id: str
    filled: bool
    ad: SelectedAd | None = None
    no_fill_reason: str | None = None
    candidates_considered: int
    trace: list[CandidateTrace] = Field(default_factory=list)
    decision_latency_ms: float


class ErrorDetail(BaseModel):
    """The body of a failed request."""

    code: int
    message: str


class ErrorResponse(BaseModel):
    """Every non-2xx response uses this shape."""

    error: ErrorDetail
```

Create `substrate/ad_decision_service/store.py`:

```python
"""Per-day decision state: frequency counters and pacing spend.

Postgres is the system of record for campaign *configuration*. These counters are
something else — high-write, per-member, and worthless after midnight. They live in
Redis behind a protocol so tests can run against an in-memory double (ADR-0003).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

# Two days, so a counter written just before midnight survives long enough to be
# read by the request that follows it in a different timezone's day boundary.
DEFAULT_TTL_SECONDS = 172_800


def frequency_key(member_id: str, campaign_id: str, day: date) -> str:
    """Redis key holding how many times this member saw this campaign today."""
    return f"freq:{member_id}:{campaign_id}:{day.isoformat()}"


def spend_key(campaign_id: str, day: date) -> str:
    """Redis key holding how much this campaign has spent today, in micros."""
    return f"spend:{campaign_id}:{day.isoformat()}"


class DecisionStore(Protocol):
    """The state the decision path reads and writes on every served impression."""

    def impression_count(self, member_id: str, campaign_id: str, day: date) -> int:
        """How many impressions of `campaign_id` this member has seen on `day`."""
        ...

    def daily_spend_micros(self, campaign_id: str, day: date) -> int:
        """How much `campaign_id` has spent on `day`, in micros."""
        ...

    def record_impression(
        self, member_id: str, campaign_id: str, day: date, price_micros: int
    ) -> None:
        """Advance both the frequency counter and the day's spend."""
        ...


class InMemoryDecisionStore:
    """Process-local store. Used by tests, and as a fallback when Redis is down."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def impression_count(self, member_id: str, campaign_id: str, day: date) -> int:
        """How many impressions of `campaign_id` this member has seen on `day`."""
        return self._counts.get(frequency_key(member_id, campaign_id, day), 0)

    def daily_spend_micros(self, campaign_id: str, day: date) -> int:
        """How much `campaign_id` has spent on `day`, in micros."""
        return self._counts.get(spend_key(campaign_id, day), 0)

    def record_impression(
        self, member_id: str, campaign_id: str, day: date, price_micros: int
    ) -> None:
        """Advance both the frequency counter and the day's spend."""
        freq = frequency_key(member_id, campaign_id, day)
        spend = spend_key(campaign_id, day)
        self._counts[freq] = self._counts.get(freq, 0) + 1
        self._counts[spend] = self._counts.get(spend, 0) + price_micros


class RedisClient(Protocol):
    """The three Redis commands this service needs. Keeps the seam narrow."""

    def get(self, key: str) -> str | None: ...
    def incrby(self, key: str, amount: int) -> int: ...
    def expire(self, key: str, seconds: int) -> None: ...


class RedisDecisionStore:
    """Production store: day-scoped keys that expire themselves."""

    def __init__(self, redis: RedisClient, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    def _read_int(self, key: str) -> int:
        raw = self._redis.get(key)
        return 0 if raw is None else int(raw)

    def _bump(self, key: str, amount: int) -> None:
        self._redis.incrby(key, amount)
        self._redis.expire(key, self._ttl_seconds)

    def impression_count(self, member_id: str, campaign_id: str, day: date) -> int:
        """How many impressions of `campaign_id` this member has seen on `day`."""
        return self._read_int(frequency_key(member_id, campaign_id, day))

    def daily_spend_micros(self, campaign_id: str, day: date) -> int:
        """How much `campaign_id` has spent on `day`, in micros."""
        return self._read_int(spend_key(campaign_id, day))

    def record_impression(
        self, member_id: str, campaign_id: str, day: date, price_micros: int
    ) -> None:
        """Advance both the frequency counter and the day's spend."""
        self._bump(frequency_key(member_id, campaign_id, day), 1)
        self._bump(spend_key(campaign_id, day), price_micros)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m uv run pytest tests/substrate/ad_decision_service -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock substrate/ad_decision_service tests/substrate/ad_decision_service
git commit -m "feat: ad-decision-service config, schemas, and Redis-backed decision store"
```

---

### Task 2: The decision filter chain

**Files:**
- Create: `substrate/ad_decision_service/decisioning.py`, `tests/substrate/ad_decision_service/test_decisioning.py`

**Interfaces:**
- Consumes: `schemas.AdRequest`, `schemas.MemberContext`, `schemas.Slot`, `schemas.FilterReason`, `store.DecisionStore`
- Produces:
  - `decisioning.Candidate` — a frozen dataclass built from a campaign-service `CampaignRead` JSON object via `Candidate.from_api(payload: dict[str, Any]) -> Candidate`, with fields `id: str`, `name: str`, `advertiser: str`, `status: str`, `daily_budget_micros: int`, `frequency_cap_per_day: int`, `targeting: dict[str, list[str]]`, `brand_safety_exclusions: list[str]`, `starts_at: datetime`, `ends_at: datetime`, `creatives: list[CandidateCreative]`
  - `decisioning.CandidateCreative` — frozen dataclass `id: str`, `name: str`, `duration_seconds: int`, `asset_url: str`
  - `decisioning.pacing_allowance_micros(daily_budget_micros: int, now: datetime) -> int`
  - `decisioning.evaluate(candidate, request, store, now, price_micros, pacing_enabled) -> FilterReason`
  - `decisioning.Outcome` — frozen dataclass `winner: Candidate | None`, `creative: CandidateCreative | None`, `trace: list[tuple[Candidate, FilterReason]]`
  - `decisioning.select(candidates, request, store, now, price_micros, pacing_enabled) -> Outcome`

**Rules, in order.** A candidate is dropped at the first rule it fails:

| Order | Rule | Reason on failure |
|---|---|---|
| 1 | `status == "active"` | `not_active` |
| 2 | `starts_at <= now <= ends_at` | `outside_flight_window` |
| 3 | Each non-empty targeting list must contain the request's value: `countries` ∋ member country, `device_types` ∋ member device, `content_ratings` ∋ slot rating. An empty list means "unrestricted". | `targeting_mismatch` |
| 4 | No slot content category appears in `brand_safety_exclusions` (case-insensitive) | `brand_safety_excluded` |
| 5 | `store.impression_count(...) < frequency_cap_per_day` | `frequency_capped` |
| 6 | Pacing (when enabled): `spend + price <= daily_budget * fraction_of_day_elapsed`, floored at one impression's price so a day can open | `pacing_throttled` |
| 7 | At least one creative fits the slot (`creative.duration_seconds <= slot.duration_seconds`) | `no_creative` |
| — | otherwise | `eligible` |

Among eligible candidates the winner is the one with the **most daily budget left to spend** (`daily_budget_micros - spend`), ties broken by campaign id for determinism. That keeps under-delivering campaigns competitive instead of letting whichever campaign sorts first drain its budget at 9am.

- [ ] **Step 1: Write the failing filter tests**

Create `tests/substrate/ad_decision_service/test_decisioning.py`:

```python
"""Each rule in the filter chain, exercised on its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from substrate.ad_decision_service.decisioning import (
    Candidate,
    evaluate,
    pacing_allowance_micros,
    select,
)
from substrate.ad_decision_service.schemas import AdRequest, MemberContext, Slot
from substrate.ad_decision_service.store import InMemoryDecisionStore

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)  # exactly half the day elapsed
PRICE = 2_000


def campaign_payload(**overrides: Any) -> dict[str, Any]:
    """A campaign-service `CampaignRead` body, as the HTTP client returns it."""
    payload: dict[str, Any] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Stranger Things S5 Launch",
        "advertiser": "Acme Snacks",
        "status": "active",
        "budget_micros": 500_000_000,
        "daily_budget_micros": 50_000_000,
        "frequency_cap_per_day": 3,
        "targeting": {
            "countries": ["US", "CA"],
            "device_types": ["tv"],
            "content_ratings": ["TV-14"],
        },
        "brand_safety_exclusions": ["news", "true-crime"],
        "starts_at": (NOW - timedelta(days=1)).isoformat(),
        "ends_at": (NOW + timedelta(days=29)).isoformat(),
        "created_at": (NOW - timedelta(days=1)).isoformat(),
        "creatives": [
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "campaign_id": "11111111-1111-1111-1111-111111111111",
                "name": "30s hero spot",
                "duration_seconds": 30,
                "asset_url": "https://cdn.example/hero.mp4",
            }
        ],
    }
    payload.update(overrides)
    return payload


def ad_request(**slot_overrides: Any) -> AdRequest:
    """An ad request that clears every rule against the default campaign."""
    slot: dict[str, Any] = {
        "slot_id": "slot-1",
        "duration_seconds": 30,
        "content_rating": "TV-14",
        "content_categories": ["drama"],
    }
    slot.update(slot_overrides)
    return AdRequest(
        member=MemberContext(member_id="member-1", country="US", device_type="tv"),
        slot=Slot(**slot),
    )


def check(candidate_payload: dict[str, Any], request: AdRequest, store: Any = None) -> str:
    """Run one candidate through the chain and return the reason it earned."""
    return evaluate(
        Candidate.from_api(candidate_payload),
        request,
        store or InMemoryDecisionStore(),
        now=NOW,
        price_micros=PRICE,
        pacing_enabled=True,
    )


def test_a_matching_campaign_is_eligible() -> None:
    assert check(campaign_payload(), ad_request()) == "eligible"


def test_paused_campaigns_are_not_active() -> None:
    assert check(campaign_payload(status="paused"), ad_request()) == "not_active"


def test_a_campaign_whose_flight_has_ended_is_out_of_window() -> None:
    ended = campaign_payload(
        starts_at=(NOW - timedelta(days=10)).isoformat(),
        ends_at=(NOW - timedelta(days=1)).isoformat(),
    )
    assert check(ended, ad_request()) == "outside_flight_window"


def test_targeting_rejects_the_wrong_country_device_or_rating() -> None:
    request = ad_request()
    request.member.country = "GB"
    assert check(campaign_payload(), request) == "targeting_mismatch"

    request = ad_request()
    request.member.device_type = "mobile"
    assert check(campaign_payload(), request) == "targeting_mismatch"

    assert check(campaign_payload(), ad_request(content_rating="TV-MA")) == "targeting_mismatch"


def test_an_empty_targeting_list_means_unrestricted() -> None:
    unrestricted = campaign_payload(
        targeting={"countries": [], "device_types": [], "content_ratings": []}
    )
    request = ad_request(content_rating="TV-MA")
    request.member.country = "GB"
    assert check(unrestricted, request) == "eligible"


def test_brand_safety_excludes_a_campaign_from_flagged_content() -> None:
    request = ad_request(content_categories=["True-Crime"])
    assert check(campaign_payload(), request) == "brand_safety_excluded"


def test_a_member_at_the_frequency_cap_is_capped() -> None:
    store = InMemoryDecisionStore()
    for _ in range(3):
        store.record_impression(
            "member-1", "11111111-1111-1111-1111-111111111111", NOW.date(), PRICE
        )
    assert check(campaign_payload(), ad_request(), store) == "frequency_capped"


def test_a_campaign_spending_ahead_of_pace_is_throttled() -> None:
    store = InMemoryDecisionStore()
    # Half the day has elapsed, so the allowance is half of 1_000_000 micros.
    ahead = campaign_payload(daily_budget_micros=1_000_000)
    for _ in range(300):  # 600_000 micros spent against a 500_000 allowance
        store.record_impression(
            "other-member", "11111111-1111-1111-1111-111111111111", NOW.date(), PRICE
        )
    assert check(ahead, ad_request(), store) == "pacing_throttled"


def test_pacing_allows_the_first_impression_of_the_day() -> None:
    midnight = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
    assert pacing_allowance_micros(1_000_000, midnight) == 0
    reason = evaluate(
        Candidate.from_api(campaign_payload(daily_budget_micros=1_000_000)),
        ad_request(),
        InMemoryDecisionStore(),
        now=midnight,
        price_micros=PRICE,
        pacing_enabled=True,
    )
    assert reason == "eligible"


def test_a_slot_too_short_for_every_creative_has_no_creative() -> None:
    assert check(campaign_payload(), ad_request(duration_seconds=15)) == "no_creative"


def test_select_returns_the_campaign_with_the_most_daily_budget_remaining() -> None:
    poor = campaign_payload(
        id="33333333-3333-3333-3333-333333333333",
        name="Small flight",
        daily_budget_micros=10_000_000,
    )
    rich = campaign_payload(name="Big flight", daily_budget_micros=40_000_000)
    outcome = select(
        [Candidate.from_api(poor), Candidate.from_api(rich)],
        ad_request(),
        InMemoryDecisionStore(),
        now=NOW,
        price_micros=PRICE,
        pacing_enabled=True,
    )
    assert outcome.winner is not None
    assert outcome.winner.name == "Big flight"
    assert outcome.creative is not None
    assert outcome.creative.duration_seconds == 30
    assert {reason for _, reason in outcome.trace} == {"eligible"}


def test_select_reports_no_winner_and_a_full_trace_when_everything_is_filtered() -> None:
    outcome = select(
        [Candidate.from_api(campaign_payload(status="paused"))],
        ad_request(),
        InMemoryDecisionStore(),
        now=NOW,
        price_micros=PRICE,
        pacing_enabled=True,
    )
    assert outcome.winner is None
    assert [reason for _, reason in outcome.trace] == ["not_active"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest tests/substrate/ad_decision_service/test_decisioning.py -v`
Expected: FAIL — `ImportError: cannot import name 'Candidate'`

- [ ] **Step 3: Implement the filter chain**

Create `substrate/ad_decision_service/decisioning.py`:

```python
"""The decision path: pure rules, no I/O.

Every rejection returns a named reason rather than a bare False. Those names end up
in the response trace, in a Prometheus label, and in the JSON logs — which is what
lets a Level 3 ops agent answer "why did fill rate drop at 14:20?" without guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from substrate.ad_decision_service.schemas import AdRequest, FilterReason
from substrate.ad_decision_service.store import DecisionStore

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class CandidateCreative:
    """An ad asset that might fill the slot."""

    id: str
    name: str
    duration_seconds: int
    asset_url: str


@dataclass(frozen=True)
class Candidate:
    """A campaign as the decision path sees it, built from campaign-service JSON."""

    id: str
    name: str
    advertiser: str
    status: str
    daily_budget_micros: int
    frequency_cap_per_day: int
    targeting: dict[str, list[str]]
    brand_safety_exclusions: list[str]
    starts_at: datetime
    ends_at: datetime
    creatives: list[CandidateCreative] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Candidate:
        """Build a candidate from one campaign-service `CampaignRead` body."""
        return cls(
            id=str(payload["id"]),
            name=payload["name"],
            advertiser=payload["advertiser"],
            status=payload["status"],
            daily_budget_micros=int(payload["daily_budget_micros"]),
            frequency_cap_per_day=int(payload["frequency_cap_per_day"]),
            targeting={k: list(v) for k, v in (payload.get("targeting") or {}).items()},
            brand_safety_exclusions=list(payload.get("brand_safety_exclusions") or []),
            starts_at=_parse_utc(payload["starts_at"]),
            ends_at=_parse_utc(payload["ends_at"]),
            creatives=[
                CandidateCreative(
                    id=str(creative["id"]),
                    name=creative["name"],
                    duration_seconds=int(creative["duration_seconds"]),
                    asset_url=creative["asset_url"],
                )
                for creative in payload.get("creatives") or []
            ],
        )


def _parse_utc(value: str | datetime) -> datetime:
    """Coerce an API timestamp to an aware UTC datetime."""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _targeting_matches(candidate: Candidate, request: AdRequest) -> bool:
    """An empty targeting list is unrestricted; a populated one must contain the value."""
    checks = (
        (candidate.targeting.get("countries", []), request.member.country),
        (candidate.targeting.get("device_types", []), request.member.device_type),
        (candidate.targeting.get("content_ratings", []), request.slot.content_rating),
    )
    return all(not allowed or value in allowed for allowed, value in checks)


def _brand_safety_allows(candidate: Candidate, request: AdRequest) -> bool:
    """Reject when the surrounding content carries a category the advertiser excluded."""
    excluded = {category.lower() for category in candidate.brand_safety_exclusions}
    return not excluded.intersection(
        category.lower() for category in request.slot.content_categories
    )


def pacing_allowance_micros(daily_budget_micros: int, now: datetime) -> int:
    """How much of today's budget a campaign is allowed to have spent by `now`.

    Even pacing: budget is released linearly across the day, so a campaign cannot
    exhaust its day in the first hour and go dark through prime time.
    """
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (now.astimezone(UTC) - midnight).total_seconds()
    return int(daily_budget_micros * elapsed / SECONDS_PER_DAY)


def _fitting_creative(candidate: Candidate, request: AdRequest) -> CandidateCreative | None:
    """The longest creative that still fits the slot, or None."""
    fitting = [
        creative
        for creative in candidate.creatives
        if creative.duration_seconds <= request.slot.duration_seconds
    ]
    return max(fitting, key=lambda creative: creative.duration_seconds) if fitting else None


def evaluate(
    candidate: Candidate,
    request: AdRequest,
    store: DecisionStore,
    now: datetime,
    price_micros: int,
    pacing_enabled: bool,
) -> FilterReason:
    """Run one candidate through the chain, returning the first rule it fails."""
    if candidate.status != "active":
        return "not_active"
    if not candidate.starts_at <= now <= candidate.ends_at:
        return "outside_flight_window"
    if not _targeting_matches(candidate, request):
        return "targeting_mismatch"
    if not _brand_safety_allows(candidate, request):
        return "brand_safety_excluded"

    day = now.astimezone(UTC).date()
    seen = store.impression_count(request.member.member_id, candidate.id, day)
    if seen >= candidate.frequency_cap_per_day:
        return "frequency_capped"

    if pacing_enabled:
        spent = store.daily_spend_micros(candidate.id, day)
        allowance = pacing_allowance_micros(candidate.daily_budget_micros, now)
        # Floor the allowance at one impression so the first request of the day fills.
        if spent + price_micros > max(allowance, price_micros):
            return "pacing_throttled"

    if _fitting_creative(candidate, request) is None:
        return "no_creative"
    return "eligible"


@dataclass(frozen=True)
class Outcome:
    """The winner (if any) plus the reason every candidate earned."""

    winner: Candidate | None
    creative: CandidateCreative | None
    trace: list[tuple[Candidate, FilterReason]]


def select(
    candidates: list[Candidate],
    request: AdRequest,
    store: DecisionStore,
    now: datetime,
    price_micros: int,
    pacing_enabled: bool,
) -> Outcome:
    """Filter every candidate, then award the slot to the least-delivered campaign."""
    trace: list[tuple[Candidate, FilterReason]] = []
    eligible: list[Candidate] = []
    for candidate in candidates:
        reason = evaluate(candidate, request, store, now, price_micros, pacing_enabled)
        trace.append((candidate, reason))
        if reason == "eligible":
            eligible.append(candidate)

    if not eligible:
        return Outcome(winner=None, creative=None, trace=trace)

    day = now.astimezone(UTC).date()

    def budget_remaining(candidate: Candidate) -> tuple[int, str]:
        remaining = candidate.daily_budget_micros - store.daily_spend_micros(candidate.id, day)
        return remaining, candidate.id

    winner = max(eligible, key=budget_remaining)
    return Outcome(winner=winner, creative=_fitting_creative(winner, request), trace=trace)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m uv run pytest tests/substrate/ad_decision_service/test_decisioning.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add substrate/ad_decision_service/decisioning.py tests/substrate/ad_decision_service/test_decisioning.py
git commit -m "feat: targeting, brand-safety, frequency-cap, and pacing filter chain"
```

---

### Task 3: The campaign-service client

**Files:**
- Create: `substrate/ad_decision_service/campaign_client.py`, `tests/substrate/ad_decision_service/test_campaign_client.py`

**Interfaces:**
- Consumes: `config.settings`, `decisioning.Candidate`
- Produces:
  - `campaign_client.CampaignServiceError(Exception)` — raised when the upstream is unreachable or returns non-2xx
  - `campaign_client.CampaignClient` with `__init__(self, base_url: str, timeout_seconds: float)`, `fetch_active_campaigns(self) -> list[Candidate]`, `close(self) -> None`
  - `campaign_client.build_client(transport: httpx.BaseTransport | None = None) -> CampaignClient` — the FastAPI dependency, overridden in tests

The client GETs `{base_url}/campaigns?status=active`. Non-2xx and transport errors both surface as `CampaignServiceError`; `main.py` turns that into a typed 503 rather than a bare 500.

- [ ] **Step 1: Write the failing client tests**

Create `tests/substrate/ad_decision_service/test_campaign_client.py`:

```python
"""The client that reads active campaigns from campaign-service over HTTP."""

from __future__ import annotations

import httpx
import pytest

from substrate.ad_decision_service.campaign_client import CampaignClient, CampaignServiceError

CAMPAIGN_BODY = {
    "id": "11111111-1111-1111-1111-111111111111",
    "name": "Stranger Things S5 Launch",
    "advertiser": "Acme Snacks",
    "status": "active",
    "budget_micros": 500_000_000,
    "daily_budget_micros": 50_000_000,
    "frequency_cap_per_day": 3,
    "targeting": {"countries": ["US"], "device_types": ["tv"], "content_ratings": ["TV-14"]},
    "brand_safety_exclusions": ["news"],
    "starts_at": "2026-07-20T00:00:00+00:00",
    "ends_at": "2026-08-20T00:00:00+00:00",
    "created_at": "2026-07-20T00:00:00+00:00",
    "creatives": [
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "campaign_id": "11111111-1111-1111-1111-111111111111",
            "name": "30s hero spot",
            "duration_seconds": 30,
            "asset_url": "https://cdn.example/hero.mp4",
        }
    ],
}


def client_with(handler: object) -> CampaignClient:
    """A CampaignClient whose transport is a local handler, never a socket."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return CampaignClient("http://campaign-service:8000", timeout_seconds=1.0, transport=transport)


def test_it_requests_only_active_campaigns_and_maps_them_to_candidates() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[CAMPAIGN_BODY])

    candidates = client_with(handler).fetch_active_campaigns()

    assert seen["url"] == "http://campaign-service:8000/campaigns?status=active"
    assert len(candidates) == 1
    assert candidates[0].advertiser == "Acme Snacks"
    assert candidates[0].creatives[0].duration_seconds == 30


def test_an_upstream_error_status_raises_campaign_service_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"code": 500, "message": "boom"}})

    with pytest.raises(CampaignServiceError):
        client_with(handler).fetch_active_campaigns()


def test_a_transport_failure_raises_campaign_service_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(CampaignServiceError):
        client_with(handler).fetch_active_campaigns()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest tests/substrate/ad_decision_service/test_campaign_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'substrate.ad_decision_service.campaign_client'`

- [ ] **Step 3: Implement the client**

Create `substrate/ad_decision_service/campaign_client.py`:

```python
"""HTTP client for campaign-service.

The decision path reads campaigns through the public API, not the database. That is
deliberate: it keeps campaign-service the single writer of its schema, which is the
condition ADR-0002 named for deferring Alembic.
"""

from __future__ import annotations

import httpx

from substrate.ad_decision_service.config import settings
from substrate.ad_decision_service.decisioning import Candidate


class CampaignServiceError(Exception):
    """campaign-service was unreachable or answered with an error status."""


class CampaignClient:
    """Reads the active campaign set that the decision path filters."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout_seconds, transport=transport
        )

    def fetch_active_campaigns(self) -> list[Candidate]:
        """Fetch every campaign currently in flight, as decision candidates."""
        try:
            response = self._client.get("/campaigns", params={"status": "active"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CampaignServiceError(str(exc)) from exc
        return [Candidate.from_api(payload) for payload in response.json()]

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._client.close()


def build_client() -> CampaignClient:
    """FastAPI dependency returning the process-wide client. Overridden in tests."""
    return _client


_client = CampaignClient(settings.campaign_service_url, settings.request_timeout_seconds)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m uv run pytest tests/substrate/ad_decision_service/test_campaign_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add substrate/ad_decision_service/campaign_client.py tests/substrate/ad_decision_service/test_campaign_client.py
git commit -m "feat: campaign-service HTTP client with typed upstream failures"
```

---

### Task 4: The FastAPI application

**Files:**
- Create: `substrate/ad_decision_service/main.py`, `tests/substrate/ad_decision_service/conftest.py`, `tests/substrate/ad_decision_service/test_api.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3
- Produces `app: FastAPI` exposing:

| Method | Path | Success | Failure |
|---|---|---|---|
| GET | `/health` | 200 `{"status": "ok", "service": "ad-decision-service"}` | — |
| POST | `/ad-request` | 200 `AdDecision` (filled or explained no-fill) | 422 `ErrorResponse` on an invalid request body; 503 `ErrorResponse` when campaign-service is unreachable |
| GET | `/metrics` | 200 Prometheus exposition | — |

- Also produces the dependency seams `get_store() -> DecisionStore` and `build_client()` (from Task 3), both overridden in tests.
- Metrics: the shared `http_requests_total{service,endpoint,method,status}` and `http_request_duration_seconds{service,endpoint}` middleware pair, plus `ad_decisions_total{service,outcome}` (outcome = `filled` | `no_fill`) and `ad_candidates_filtered_total{service,reason}`.
- Every decision emits one structured log line with `service`, `endpoint`, `latency_ms`, `member_id`, `slot_id`, `filled`, `campaign_id`, `candidates_considered`.

- [ ] **Step 1: Write the conftest**

Create `tests/substrate/ad_decision_service/conftest.py`:

```python
"""Fixtures backing ad-decision-service tests. No Redis, no live campaign-service."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from substrate.ad_decision_service.campaign_client import CampaignClient, build_client
from substrate.ad_decision_service.main import app, get_store
from substrate.ad_decision_service.store import InMemoryDecisionStore


class StubCampaignClient:
    """Returns a fixed campaign set, or raises whatever the test asks it to."""

    def __init__(self, campaigns: list[dict[str, Any]], error: Exception | None = None) -> None:
        self.campaigns = campaigns
        self.error = error

    def fetch_active_campaigns(self) -> list[Any]:
        """Mimic CampaignClient.fetch_active_campaigns against in-test data."""
        from substrate.ad_decision_service.decisioning import Candidate

        if self.error is not None:
            raise self.error
        return [Candidate.from_api(payload) for payload in self.campaigns]

    def close(self) -> None:
        """No pool to release."""


@pytest.fixture
def active_campaign() -> dict[str, Any]:
    """One in-flight campaign with a 30-second creative."""
    now = datetime.now(UTC)
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Stranger Things S5 Launch",
        "advertiser": "Acme Snacks",
        "status": "active",
        "budget_micros": 500_000_000,
        "daily_budget_micros": 50_000_000,
        "frequency_cap_per_day": 2,
        "targeting": {
            "countries": ["US"],
            "device_types": ["tv"],
            "content_ratings": ["TV-14"],
        },
        "brand_safety_exclusions": ["true-crime"],
        "starts_at": (now - timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=29)).isoformat(),
        "created_at": (now - timedelta(days=1)).isoformat(),
        "creatives": [
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "campaign_id": "11111111-1111-1111-1111-111111111111",
                "name": "30s hero spot",
                "duration_seconds": 30,
                "asset_url": "https://cdn.example/hero.mp4",
            }
        ],
    }


@pytest.fixture
def ad_request_body() -> dict[str, Any]:
    """An ad request that fills against `active_campaign`."""
    return {
        "member": {"member_id": "member-1", "country": "US", "device_type": "tv"},
        "slot": {
            "slot_id": "slot-1",
            "duration_seconds": 30,
            "content_rating": "TV-14",
            "content_categories": ["drama"],
        },
    }


@pytest.fixture
def store() -> InMemoryDecisionStore:
    """Frequency and pacing counters, process-local and empty."""
    return InMemoryDecisionStore()


@pytest.fixture
def make_client(store: InMemoryDecisionStore) -> Iterator[Any]:
    """Build a TestClient whose campaign set (or upstream failure) the test chooses.

    The client is constructed without entering its context manager on purpose: the
    lifespan would open a real Redis connection, and these tests deliberately need
    no infrastructure.
    """

    def factory(
        campaigns: list[dict[str, Any]], error: Exception | None = None
    ) -> TestClient:
        stub = StubCampaignClient(campaigns, error)
        app.dependency_overrides[build_client] = lambda: stub
        app.dependency_overrides[get_store] = lambda: store
        return TestClient(app)

    yield factory
    app.dependency_overrides.clear()


__all__ = ["CampaignClient", "StubCampaignClient", "active_campaign", "make_client", "store"]
```

- [ ] **Step 2: Write the failing API tests**

Create `tests/substrate/ad_decision_service/test_api.py`:

```python
"""Endpoint contracts: a fill, each no-fill reason, and the failure paths."""

from __future__ import annotations

from typing import Any

from substrate.ad_decision_service.campaign_client import CampaignServiceError
from substrate.ad_decision_service.store import InMemoryDecisionStore


def test_health_reports_the_service_name(make_client: Any) -> None:
    response = make_client([]).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ad-decision-service"}


def test_a_matching_request_fills_the_slot(
    make_client: Any, active_campaign: dict[str, Any], ad_request_body: dict[str, Any]
) -> None:
    response = make_client([active_campaign]).post("/ad-request", json=ad_request_body)
    assert response.status_code == 200
    body = response.json()
    assert body["filled"] is True
    assert body["ad"]["advertiser"] == "Acme Snacks"
    assert body["ad"]["asset_url"] == "https://cdn.example/hero.mp4"
    assert body["candidates_considered"] == 1
    assert body["trace"] == [
        {
            "campaign_id": "11111111-1111-1111-1111-111111111111",
            "campaign_name": "Stranger Things S5 Launch",
            "reason": "eligible",
        }
    ]


def test_serving_records_the_impression_against_the_frequency_cap(
    make_client: Any,
    active_campaign: dict[str, Any],
    ad_request_body: dict[str, Any],
    store: InMemoryDecisionStore,
) -> None:
    client = make_client([active_campaign])
    for _ in range(2):  # frequency_cap_per_day is 2
        assert client.post("/ad-request", json=ad_request_body).json()["filled"] is True

    third = client.post("/ad-request", json=ad_request_body).json()
    assert third["filled"] is False
    assert third["no_fill_reason"] == "frequency_capped"
    assert store.daily_spend_micros("11111111-1111-1111-1111-111111111111", _today()) == 4_000


def test_brand_safety_produces_an_explained_no_fill(
    make_client: Any, active_campaign: dict[str, Any], ad_request_body: dict[str, Any]
) -> None:
    ad_request_body["slot"]["content_categories"] = ["true-crime"]
    body = make_client([active_campaign]).post("/ad-request", json=ad_request_body).json()
    assert body["filled"] is False
    assert body["no_fill_reason"] == "brand_safety_excluded"
    assert body["ad"] is None


def test_no_candidates_at_all_is_a_clean_no_fill(
    make_client: Any, ad_request_body: dict[str, Any]
) -> None:
    body = make_client([]).post("/ad-request", json=ad_request_body).json()
    assert body["filled"] is False
    assert body["no_fill_reason"] == "no_candidates"
    assert body["candidates_considered"] == 0


def test_an_invalid_request_returns_the_typed_error_envelope(
    make_client: Any, ad_request_body: dict[str, Any]
) -> None:
    ad_request_body["slot"]["duration_seconds"] = 0
    response = make_client([]).post("/ad-request", json=ad_request_body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == 422


def test_an_unreachable_campaign_service_returns_503_not_500(
    make_client: Any, ad_request_body: dict[str, Any]
) -> None:
    client = make_client([], CampaignServiceError("connection refused"))
    response = client.post("/ad-request", json=ad_request_body)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == 503
    assert "campaign-service" in response.json()["error"]["message"]


def test_metrics_expose_decision_counters(
    make_client: Any, active_campaign: dict[str, Any], ad_request_body: dict[str, Any]
) -> None:
    client = make_client([active_campaign])
    client.post("/ad-request", json=ad_request_body)
    body = client.get("/metrics").text
    assert "ad_decisions_total" in body
    assert 'outcome="filled"' in body
    assert "ad_candidates_filtered_total" in body
    assert "http_requests_total" in body


def _today() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC).date()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest tests/substrate/ad_decision_service/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'substrate.ad_decision_service.main'`

- [ ] **Step 4: Implement the application**

Create `substrate/ad_decision_service/main.py`:

```python
"""ad-decision-service HTTP API.

The serving path. One ad request in, one decision out: eligibility, targeting,
brand safety, frequency capping, and budget pacing, in that order, over the active
campaign set read from campaign-service.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import redis as redis_lib
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from substrate.ad_decision_service.campaign_client import (
    CampaignClient,
    CampaignServiceError,
    build_client,
)
from substrate.ad_decision_service.config import settings
from substrate.ad_decision_service.decisioning import select
from substrate.ad_decision_service.schemas import (
    AdDecision,
    AdRequest,
    CandidateTrace,
    ErrorResponse,
    SelectedAd,
)
from substrate.ad_decision_service.store import DecisionStore, RedisDecisionStore
from substrate.shared.logging import configure_logging, log_context

logger = logging.getLogger("ad_decision_service.api")

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled.",
    ["service", "endpoint", "method", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency.",
    ["service", "endpoint"],
)
DECISIONS = Counter(
    "ad_decisions_total",
    "Ad decisions by outcome.",
    ["service", "outcome"],
)
FILTERED = Counter(
    "ad_candidates_filtered_total",
    "Candidate campaigns by the rule that decided them.",
    ["service", "reason"],
)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorResponse, "description": "campaign-service unavailable"}
}

_store: DecisionStore = RedisDecisionStore(
    redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
)


def get_store() -> DecisionStore:
    """FastAPI dependency returning the decision store. Overridden in tests."""
    return _store


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure logging before serving traffic."""
    configure_logging(settings.service_name)
    logger.info("ad-decision-service ready")
    yield


app = FastAPI(
    title="ad-decision-service",
    version="0.1.0",
    summary="Targeting, brand safety, frequency capping, and budget pacing on the serving path.",
    lifespan=lifespan,
)


@app.middleware("http")
async def observe_request(
    request: Request,
    call_next: Callable[[Request], Coroutine[Any, Any, Response]],
) -> Response:
    """Record latency and outcome for every request, in metrics and in logs."""
    route: Any = request.scope.get("route")
    endpoint: str = route.path if route is not None else request.url.path
    started = time.perf_counter()
    response = await call_next(request)
    latency_s = time.perf_counter() - started

    REQUESTS.labels(settings.service_name, endpoint, request.method, response.status_code).inc()
    LATENCY.labels(settings.service_name, endpoint).observe(latency_s)
    log_context(
        logger,
        "request handled",
        service=settings.service_name,
        endpoint=endpoint,
        method=request.method,
        status=response.status_code,
        latency_ms=round(latency_s * 1000, 3),
    )
    return response


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


@app.post("/ad-request", response_model=AdDecision, responses=ERROR_RESPONSES)
def decide(
    ad_request: AdRequest,
    client: CampaignClient = Depends(build_client),
    store: DecisionStore = Depends(get_store),
) -> AdDecision:
    """Fill one ad slot, or explain in the response why nothing could fill it."""
    started = time.perf_counter()
    now = datetime.now(UTC)

    try:
        candidates = client.fetch_active_campaigns()
    except CampaignServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"campaign-service unavailable: {exc}",
        ) from exc

    outcome = select(
        candidates,
        ad_request,
        store,
        now=now,
        price_micros=settings.impression_price_micros,
        pacing_enabled=settings.pacing_enabled,
    )

    trace = [
        CandidateTrace(campaign_id=uuid.UUID(candidate.id), campaign_name=candidate.name, reason=r)
        for candidate, r in outcome.trace
    ]
    for _, reason in outcome.trace:
        FILTERED.labels(settings.service_name, reason).inc()

    ad: SelectedAd | None = None
    if outcome.winner is not None and outcome.creative is not None:
        store.record_impression(
            ad_request.member.member_id,
            outcome.winner.id,
            now.date(),
            settings.impression_price_micros,
        )
        ad = SelectedAd(
            campaign_id=uuid.UUID(outcome.winner.id),
            campaign_name=outcome.winner.name,
            advertiser=outcome.winner.advertiser,
            creative_id=uuid.UUID(outcome.creative.id),
            creative_name=outcome.creative.name,
            asset_url=outcome.creative.asset_url,
            duration_seconds=outcome.creative.duration_seconds,
            price_micros=settings.impression_price_micros,
        )

    DECISIONS.labels(settings.service_name, "filled" if ad else "no_fill").inc()
    decision = AdDecision(
        request_id=uuid.uuid4(),
        slot_id=ad_request.slot.slot_id,
        filled=ad is not None,
        ad=ad,
        no_fill_reason=None if ad else _no_fill_reason(trace),
        candidates_considered=len(candidates),
        trace=trace,
        decision_latency_ms=round((time.perf_counter() - started) * 1000, 3),
    )

    log_context(
        logger,
        "ad decision",
        service=settings.service_name,
        endpoint="/ad-request",
        latency_ms=decision.decision_latency_ms,
        member_id=ad_request.member.member_id,
        slot_id=decision.slot_id,
        filled=decision.filled,
        campaign_id=str(ad.campaign_id) if ad else None,
        no_fill_reason=decision.no_fill_reason,
        candidates_considered=decision.candidates_considered,
    )
    return decision


def _no_fill_reason(trace: list[CandidateTrace]) -> str:
    """Summarize a no-fill as the rule that eliminated the last surviving candidate."""
    if not trace:
        return "no_candidates"
    order = [
        "pacing_throttled",
        "frequency_capped",
        "no_creative",
        "brand_safety_excluded",
        "targeting_mismatch",
        "outside_flight_window",
        "not_active",
    ]
    reasons = {entry.reason for entry in trace}
    return next((reason for reason in order if reason in reasons), "no_candidates")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m uv run pytest tests/substrate/ad_decision_service -v`
Expected: PASS (all 8 API tests plus the 21 from Tasks 1–3)

- [ ] **Step 6: Verify the whole suite is still hermetic and clean**

Run: `python -m uv run pytest && python -m uv run ruff check . && python -m uv run ruff format --check . && python -m uv run mypy tests substrate`
Expected: all green, with Docker stopped.

- [ ] **Step 7: Commit**

```bash
git add substrate/ad_decision_service/main.py tests/substrate/ad_decision_service
git commit -m "feat: ad-decision-service API with decision trace, metrics, and typed errors"
```

---

### Task 5: Containerize and wire into the stack

**Files:**
- Create: `substrate/ad_decision_service/Dockerfile`
- Modify: `docker-compose.yml`, `infra/prometheus/prometheus.yml`, `README.md`

**Interfaces:**
- Produces: an `ad-decision-service` container on the `bellwether` network, host port **8002**, scraped by Prometheus at `ad-decision-service:8000/metrics`, depending on `redis` and `campaign-service` being healthy.

- [ ] **Step 1: Write the Dockerfile**

Create `substrate/ad_decision_service/Dockerfile`:

```dockerfile
# ad-decision-service: the serving path — targeting, brand safety, capping, pacing.
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

CMD ["uvicorn", "substrate.ad_decision_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Add the service to Compose**

In `docker-compose.yml`, after the `campaign-service` block:

```yaml
  ad-decision-service:
    build:
      context: .
      dockerfile: substrate/ad_decision_service/Dockerfile
    environment:
      AD_DECISION_CAMPAIGN_SERVICE_URL: http://campaign-service:8000
      AD_DECISION_REDIS_URL: redis://redis:6379/0
    ports: ["8002:8000"]
    depends_on:
      redis:
        condition: service_healthy
      campaign-service:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\""]
      interval: 10s
      timeout: 3s
      retries: 5
```

- [ ] **Step 3: Register the Prometheus scrape target**

In `infra/prometheus/prometheus.yml`, add below the campaign-service job:

```yaml
  - job_name: ad-decision-service
    static_configs:
      - targets: ["ad-decision-service:8000"]
```

- [ ] **Step 4: Bring the stack up and verify by hand**

```bash
docker compose up -d --build ad-decision-service
curl -s localhost:8002/health
curl -s -X POST localhost:8002/ad-request -H 'content-type: application/json' \
  -d '{"member":{"member_id":"member-1","country":"US","device_type":"tv"},
       "slot":{"slot_id":"slot-1","duration_seconds":30,"content_rating":"TV-14","content_categories":["drama"]}}'
```

Expected: `/health` returns `{"status":"ok","service":"ad-decision-service"}`; the ad request returns an `AdDecision` (a no-fill with `candidates_considered: 0` is correct on an empty database — create an active campaign via `localhost:8001/campaigns` to see a fill). Check `localhost:9090/targets` shows `ad-decision-service` up, and `docker compose logs ad-decision-service` shows one JSON line per decision.

- [ ] **Step 5: Update the README quickstart**

Add `ad-decision-service` to the service/port table in `README.md` alongside campaign-service (host port 8002, docs at `localhost:8002/docs`).

- [ ] **Step 6: Commit**

```bash
git add substrate/ad_decision_service/Dockerfile docker-compose.yml infra/prometheus/prometheus.yml README.md
git commit -m "feat: containerize ad-decision-service and register Prometheus scrape target"
```

---

### Task 6: Documentation and definition of done

**Files:**
- Create: `docs/adr/0003-redis-for-decision-state.md`, `docs/devlog/day-03.md`
- Modify: `docs/site/index.html`

- [ ] **Step 1: Write ADR-0003**

Follow the format of `docs/adr/0002-create-all-over-alembic.md`. Decision: frequency counters and daily pacing spend live in Redis under day-scoped, self-expiring keys; campaign-service's Postgres stays the system of record for configuration. Context: these writes happen on every served impression, are per-member, and are worthless after the day rolls over — the access pattern is a counter with a TTL, not a row. Consequences: decision state is lost if Redis is flushed (acceptable — the worst case is a member briefly seeing one extra ad), and spend here is an estimate, not billing. **Record the trigger that flips it:** when Day 4's event-service makes impressions durable, billable spend moves to Postgres and Redis keeps only the hot counters.

- [ ] **Step 2: Update the running doc**

In `docs/site/index.html`:
1. Top bar: `DAY <b>02</b>` → `DAY <b>03</b>`; footer `DAY 02 / 30` → `DAY 03 / 30`.
2. Pod head: `2 shipped · 28 queued` → `3 shipped · 27 queued`.
3. Pod strip: add `nohead` to the Day 2 segment's class (`seg shipped nohead`) and change the Day 3 segment to `class="seg shipped"` so the playhead moves to Day 3.
4. Tracker table row `03`: `<td class="st plan">○ QUEUED</td>` → `<td class="st done">● SHIPPED</td>`.
5. Add a "Day 3 — the decision path" Mermaid flowchart to the Level 0 section, after the Day 2 ER diagram:

```html
    <h3>Day 3 — the decision path</h3>
    <pre class="mermaid">
flowchart LR
    REQ["POST /ad-request&lt;br/&gt;member + slot"] --> FETCH["active campaigns&lt;br/&gt;(campaign-service HTTP)"]
    FETCH --> F1{"in flight?"}
    F1 -->|no| X1["not_active /&lt;br/&gt;outside_flight_window"]
    F1 -->|yes| F2{"targeting match?"}
    F2 -->|no| X2["targeting_mismatch"]
    F2 -->|yes| F3{"brand safe?"}
    F3 -->|no| X3["brand_safety_excluded"]
    F3 -->|yes| F4{"under frequency cap?"}
    F4 -->|no| X4["frequency_capped"]
    F4 -->|yes| F5{"within pacing allowance?"}
    F5 -->|no| X5["pacing_throttled"]
    F5 -->|yes| WIN["most daily budget remaining wins"]
    WIN --> REC["record impression&lt;br/&gt;(Redis: freq + spend)"]
    REC --> RES["AdDecision + full candidate trace"]
    X1 & X2 & X3 & X4 & X5 --> RES
    </pre>
```

6. Add a decision card to SEQ 04, above the ADR-0002 card:

```html
    <div class="card">
      <h3>ADR-0003 — Redis holds the decision state, Postgres holds the truth</h3>
      <p>Frequency counters and daily pacing spend are written on every served impression, scoped per member, and worthless after midnight — a counter with a TTL, not a row. They live in Redis under day-scoped keys that expire themselves; campaign-service stays the system of record for configuration. The trigger that flips this: Day 4's event-service, when impressions become durable and spend becomes billable.</p>
    </div>
```

- [ ] **Step 3: Write the devlog**

Create `docs/devlog/day-03.md` following the Day 2 format exactly — `# Day 3 — ad-decision-service`, `**Level:** 0 · **Date:** 2026-07-21`, then `## Shipped`, `## Decisions`, `## For the video`, `## Tomorrow`. The video shot list should cover: reading the filter chain top to bottom in `decisioning.py` and naming each rule from the posting; a `POST /ad-request` that fills; the same request repeated until `frequency_capped` appears in the trace; a brand-safety no-fill; `docker compose logs ad-decision-service` showing the decision JSON line; the Prometheus `ad_candidates_filtered_total` counter broken out by reason; and ADR-0003's flip trigger. "Tomorrow" points at Day 4 — event-service plus the observability stack.

- [ ] **Step 4: Full verification**

Run: `python -m uv run pytest && python -m uv run ruff check . && python -m uv run ruff format --check . && python -m uv run mypy tests substrate`
Then: `docker compose ps` — campaign-service and ad-decision-service both healthy.

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "docs: ADR-0003, day-03 devlog, running doc updated for ad-decision-service"
```

---

## Execution notes (what the plan missed)

Recorded during execution, so the next plan does not repeat these:

1. **Prometheus registry collision.** The plan had ad-decision-service define `http_requests_total` and `http_request_duration_seconds` itself, copying campaign-service. Prometheus keeps one global registry per process, so the second definition raises `Duplicated timeseries` as soon as both services are imported — which the test suite does. Fixed by extracting `substrate/shared/observability.py` with the metrics defined once and an `install_request_observability(app, service_name, logger)` helper; campaign-service was migrated onto it.
2. **Duplicate test module basenames.** Two services each owning `tests/.../test_api.py` and `conftest.py` collides in pytest's default import mode *and* in mypy. Fixed by adding `__init__.py` to every directory under `tests/`.
3. **`RedisClient` protocol was too strict.** redis-py names its parameters `name`/`time` and returns `bool` from `expire`, so a protocol declaring `key: str` / `-> None` does not match. Fixed by making the protocol's parameters positional-only and widening the return types.
4. `build_client()` takes no arguments — the `transport` seam belongs to `CampaignClient.__init__` only.
