# BELLWETHER Level 0 / Day 5 — traffic-simulator + Level 0 quality gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `traffic-simulator` — realistic, seeded ad-request load driven through the whole substrate, with five **injectable failure modes** switchable live over a control API — then run the Level 0 quality gate and publish its numbers.

**Architecture:** Three pure modules and one thin shell. `population.py` builds member contexts and slots from a seeded RNG, so a run is reproducible. `scenarios.py` defines the five failure modes as data plus the mutations each applies. `driver.py` runs one tick of the loop — ad request in, decision out, delivery report back to event-service — against injected client protocols, so the whole thing is testable with no network. `main.py` is a FastAPI control plane that runs the loop in the background and lets a human (or, from Level 3, an ops agent) switch scenarios at runtime.

**The load-bearing idea (ADR-0005):** failure modes are injected by **changing real configuration through public APIs**, never by mocking or by a flag the substrate reads. `bad_config_deploy` genuinely PATCHes every campaign's targeting; `budget_runaway` genuinely raises a daily budget. The incident is real, so the Level 3 RCA agent will diagnose a real cause rather than a staged one.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pydantic-settings, httpx, prometheus-client, pytest.

## Global Constraints

- Python 3.11+; type hints on all functions; `mypy --strict` must pass on `tests` and `substrate`
- Ruff clean (line length 100); `ruff format --check` clean; conventional commits
- Request metrics and the request log line come from `substrate/shared/observability.py` via `install_request_observability(app, service_name, logger)`. **Never redefine `http_requests_total` or `http_request_duration_seconds`**
- Test helpers taking arbitrary overrides use `**overrides: object`, never `Any` (ANN401); fixtures are annotated with their concrete type, never `Any`
- Ads-domain naming only: `ad_request`, `member`, `slot`, `impression`, `click`, `fill`, `no_fill`, `scenario`, `pacing`
- **Tests must be hermetic**: the whole suite passes with Docker stopped. The driver takes client protocols; no test opens a socket
- Host ports: Postgres **5433**, Redis **6380**, campaign-service **8001**, ad-decision-service **8002**, event-service **8003**, traffic-simulator **8004**, Prometheus **9090**, Grafana **3000**
- Every directory under `tests/` needs an `__init__.py`
- `uv` is invoked as `python -m uv`
- Grafana and Prometheus do **not** reload provisioning on their own — `docker compose restart prometheus grafana` after touching either
- Verification gates are run **unpiped**, so a failure cannot hide behind `tail`'s exit status
- Definition of done: `docs/site/index.html` day tracker **and eval scoreboard** updated, `docs/devlog/day-05.md` written

## File Structure

| File | Responsibility |
|---|---|
| `substrate/traffic_simulator/config.py` | `SIM_*` settings — service URLs, rate, click probability, seed |
| `substrate/traffic_simulator/population.py` | Seeded generation of members and slots — pure |
| `substrate/traffic_simulator/scenarios.py` | The five scenarios as data, and the mutations each applies — pure |
| `substrate/traffic_simulator/clients.py` | HTTP client protocols + httpx implementations for the three services |
| `substrate/traffic_simulator/seeding.py` | Creates a realistic campaign set on first start, if none exists |
| `substrate/traffic_simulator/driver.py` | One tick: request → decision → delivery report. No I/O of its own |
| `substrate/traffic_simulator/main.py` | Control plane: run/stop, switch scenario, expose metrics |
| `platform/gates/level0_gate.py` | The Level 0 quality gate, run against the live stack |

---

### Task 1: Population and scenarios

**Files:**
- Create: `substrate/traffic_simulator/__init__.py`, `config.py`, `population.py`, `scenarios.py`
- Create: `tests/substrate/traffic_simulator/__init__.py`, `test_population.py`, `test_scenarios.py`

**Interfaces:**
- Produces:
  - `config.Settings` (env prefix `SIM_`): `service_name: str = "traffic-simulator"`, `campaign_service_url`, `ad_decision_url`, `event_service_url`, `requests_per_second: float = 5.0`, `click_probability: float = 0.08`, `seed: int = 20260721`, `request_timeout_seconds: float = 2.0`, `autostart: bool = True`; module-level `settings`
  - `population.Population` with `__init__(self, seed: int)`, `member() -> dict[str, str]`, `slot() -> dict[str, object]`, `ad_request() -> dict[str, object]`
  - `population.COUNTRIES`, `DEVICE_TYPES`, `CONTENT_RATINGS`, `CONTENT_CATEGORIES`
  - `scenarios.Scenario` — frozen dataclass: `name: str`, `summary: str`, `rate_multiplier: float`, `malformed_fraction: float`, `config_mutation: str | None`
  - `scenarios.SCENARIOS: dict[str, Scenario]` with keys `steady`, `error_burst`, `traffic_surge`, `bad_config_deploy`, `budget_runaway`
  - `scenarios.corrupt(request: dict[str, object]) -> dict[str, object]` — returns a request the API must reject with 422
  - `scenarios.get(name: str) -> Scenario` — raises `KeyError` for unknown names

The five scenarios:

| Name | rate× | malformed | config mutation | What the dashboards show |
|---|---|---|---|---|
| `steady` | 1.0 | 0.0 | — | flat fill rate, flat p95 — the baseline |
| `error_burst` | 1.0 | 0.30 | — | `http_requests_total{status="422"}` spikes; error-ratio panel lifts |
| `traffic_surge` | 10.0 | 0.0 | — | request rate and p95 latency climb together |
| `bad_config_deploy` | 1.0 | 0.0 | `retarget_all_campaigns` | fill rate collapses; the `targeting_mismatch` band takes over "why candidates lost" |
| `budget_runaway` | 1.0 | 0.0 | `inflate_one_daily_budget` | `pacing_throttled` vanishes; one campaign takes every slot; spend rate jumps |

- [ ] **Step 1: Write the failing population tests**

Create `tests/substrate/traffic_simulator/__init__.py` (empty file).

Create `tests/substrate/traffic_simulator/test_population.py`:

```python
"""Traffic is random but reproducible: same seed, same run."""

from __future__ import annotations

from substrate.traffic_simulator.population import (
    CONTENT_RATINGS,
    COUNTRIES,
    DEVICE_TYPES,
    Population,
)


def test_a_member_is_drawn_from_the_declared_dimensions() -> None:
    member = Population(seed=1).member()
    assert member["member_id"].startswith("member-")
    assert member["country"] in COUNTRIES
    assert member["device_type"] in DEVICE_TYPES


def test_a_slot_is_valid_against_the_ad_decision_schema() -> None:
    slot = Population(seed=1).slot()
    assert 0 < int(slot["duration_seconds"]) <= 180
    assert slot["content_rating"] in CONTENT_RATINGS
    assert isinstance(slot["content_categories"], list)
    assert slot["content_categories"]


def test_the_same_seed_produces_the_same_traffic() -> None:
    first = [Population(seed=7).ad_request() for _ in range(5)]
    second = [Population(seed=7).ad_request() for _ in range(5)]
    assert first == second


def test_different_seeds_produce_different_traffic() -> None:
    assert Population(seed=1).ad_request() != Population(seed=2).ad_request()


def test_an_ad_request_carries_a_member_and_a_slot() -> None:
    request = Population(seed=3).ad_request()
    assert set(request) == {"member", "slot"}


def test_members_repeat_so_frequency_capping_can_actually_bite() -> None:
    population = Population(seed=5)
    ids = {population.member()["member_id"] for _ in range(200)}
    # A small member pool is the point: an unbounded one would never hit a cap.
    assert len(ids) < 200
```

- [ ] **Step 2: Write the failing scenario tests**

Create `tests/substrate/traffic_simulator/test_scenarios.py`:

```python
"""The five failure modes, as data."""

from __future__ import annotations

import pytest

from substrate.traffic_simulator.population import Population
from substrate.traffic_simulator.scenarios import SCENARIOS, corrupt, get


def test_every_scenario_is_named_and_summarized() -> None:
    assert set(SCENARIOS) == {
        "steady",
        "error_burst",
        "traffic_surge",
        "bad_config_deploy",
        "budget_runaway",
    }
    for name, scenario in SCENARIOS.items():
        assert scenario.name == name
        assert scenario.summary


def test_steady_is_the_baseline_and_changes_nothing() -> None:
    steady = get("steady")
    assert steady.rate_multiplier == 1.0
    assert steady.malformed_fraction == 0.0
    assert steady.config_mutation is None


def test_error_burst_sends_malformed_requests_without_touching_config() -> None:
    burst = get("error_burst")
    assert burst.malformed_fraction > 0
    assert burst.config_mutation is None


def test_traffic_surge_only_raises_the_rate() -> None:
    surge = get("traffic_surge")
    assert surge.rate_multiplier > 1
    assert surge.malformed_fraction == 0.0
    assert surge.config_mutation is None


def test_the_config_scenarios_name_a_real_mutation() -> None:
    assert get("bad_config_deploy").config_mutation == "retarget_all_campaigns"
    assert get("budget_runaway").config_mutation == "inflate_one_daily_budget"


def test_an_unknown_scenario_is_a_key_error() -> None:
    with pytest.raises(KeyError):
        get("chaos_monkey")


def test_a_corrupted_request_violates_the_ad_decision_schema() -> None:
    corrupted = corrupt(Population(seed=1).ad_request())
    slot = corrupted["slot"]
    assert isinstance(slot, dict)
    # duration_seconds is `gt=0` on the Slot model, so zero is a guaranteed 422.
    assert slot["duration_seconds"] == 0


def test_corrupting_a_request_does_not_mutate_the_original() -> None:
    original = Population(seed=1).ad_request()
    before = str(original)
    corrupt(original)
    assert str(original) == before
```

- [ ] **Step 3: Run both to verify they fail**

Run: `python -m uv run pytest tests/substrate/traffic_simulator -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'substrate.traffic_simulator'`

- [ ] **Step 4: Implement config, population, and scenarios**

Create `substrate/traffic_simulator/__init__.py` (empty file).

Create `substrate/traffic_simulator/config.py`:

```python
"""Runtime configuration for traffic-simulator."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings read from `SIM_*` environment variables.

    Defaults point at host-published ports so the simulator runs outside Docker;
    inside the compose network the URLs are supplied explicitly.
    """

    model_config = SettingsConfigDict(env_prefix="SIM_")

    service_name: str = "traffic-simulator"
    campaign_service_url: str = "http://localhost:8001"
    ad_decision_url: str = "http://localhost:8002"
    event_service_url: str = "http://localhost:8003"
    requests_per_second: float = 5.0
    # Roughly an order of magnitude above real CTV click rates, so a demo shows
    # clicks within a minute instead of within an hour.
    click_probability: float = 0.08
    seed: int = 20260721
    request_timeout_seconds: float = 2.0
    autostart: bool = True


settings = Settings()
```

Create `substrate/traffic_simulator/population.py`:

```python
"""The audience the simulator pretends to serve.

Seeded on purpose. A reproducible traffic pattern means a demo can be re-run and
an incident can be replayed — which matters more here than statistical realism,
because Level 3's ops agents will be evaluated against runs that must be repeatable.
"""

from __future__ import annotations

import random

COUNTRIES = ("US", "CA", "GB", "DE", "BR")
DEVICE_TYPES = ("tv", "mobile", "tablet", "desktop")
CONTENT_RATINGS = ("TV-G", "TV-14", "TV-MA")
CONTENT_CATEGORIES = ("drama", "comedy", "documentary", "news", "true-crime", "sports")
SLOT_DURATIONS = (15, 30, 60)

# A bounded member pool, so the same member returns often enough that frequency
# capping actually engages. An unbounded pool would never hit a cap and the
# frequency_capped band would stay flat at zero forever.
MEMBER_POOL_SIZE = 120


class Population:
    """Draws members and slots from a fixed seed."""

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def member(self) -> dict[str, str]:
        """One viewer, as the decision path sees them."""
        return {
            "member_id": f"member-{self._random.randrange(MEMBER_POOL_SIZE):04d}",
            "country": self._random.choice(COUNTRIES),
            "device_type": self._random.choice(DEVICE_TYPES),
        }

    def slot(self) -> dict[str, object]:
        """One ad break, and the content surrounding it."""
        return {
            "slot_id": f"slot-{self._random.randrange(1_000):03d}",
            "duration_seconds": self._random.choice(SLOT_DURATIONS),
            "content_rating": self._random.choice(CONTENT_RATINGS),
            "content_categories": self._random.sample(CONTENT_CATEGORIES, k=2),
        }

    def ad_request(self) -> dict[str, object]:
        """One opportunity to serve an ad."""
        return {"member": self.member(), "slot": self.slot()}
```

Create `substrate/traffic_simulator/scenarios.py`:

```python
"""The failure modes the simulator can inject.

Each scenario is data, not a branch buried in the driver. Two of them change real
campaign configuration through campaign-service's public API rather than faking a
symptom — see ADR-0005. That is what makes the resulting incident diagnosable:
there is an actual cause sitting in an actual table.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    """One injectable failure mode, and the three knobs it can turn."""

    name: str
    summary: str
    rate_multiplier: float = 1.0
    malformed_fraction: float = 0.0
    config_mutation: str | None = None


SCENARIOS: dict[str, Scenario] = {
    "steady": Scenario(
        name="steady",
        summary="Baseline traffic. Nothing is wrong; this is what healthy looks like.",
    ),
    "error_burst": Scenario(
        name="error_burst",
        summary="Three in ten ad requests are malformed. The error-ratio panel lifts "
        "and the 422s are attributable to a caller, not to the service.",
        malformed_fraction=0.30,
    ),
    "traffic_surge": Scenario(
        name="traffic_surge",
        summary="Ten times the request rate. Throughput and p95 latency climb together, "
        "which is what load looks like as distinct from a fault.",
        rate_multiplier=10.0,
    ),
    "bad_config_deploy": Scenario(
        name="bad_config_deploy",
        summary="Every campaign is retargeted to a country the traffic never comes from. "
        "Fill rate collapses and targeting_mismatch takes over the loss breakdown.",
        config_mutation="retarget_all_campaigns",
    ),
    "budget_runaway": Scenario(
        name="budget_runaway",
        summary="One campaign's daily budget is inflated a thousandfold. Pacing stops "
        "throttling it and it takes every slot it is eligible for.",
        config_mutation="inflate_one_daily_budget",
    ),
}


def get(name: str) -> Scenario:
    """Look up a scenario by name, raising KeyError for anything unknown."""
    return SCENARIOS[name]


def corrupt(request: dict[str, object]) -> dict[str, object]:
    """Return a copy of `request` that the ad-decision API is required to reject.

    `duration_seconds` is `gt=0` on the Slot model, so zero is a guaranteed 422 —
    a caller error, which is exactly the signal this scenario is meant to produce.
    """
    corrupted = copy.deepcopy(request)
    slot = corrupted["slot"]
    if isinstance(slot, dict):
        slot["duration_seconds"] = 0
    return corrupted
```

- [ ] **Step 5: Run both suites to verify they pass**

Run: `python -m uv run pytest tests/substrate/traffic_simulator -v`
Expected: PASS (14 tests)

- [ ] **Step 6: Commit**

```bash
git add substrate/traffic_simulator tests/substrate/traffic_simulator
git commit -m "feat: seeded traffic population and the five injectable failure scenarios"
```

---

### Task 2: Service clients and campaign seeding

**Files:**
- Create: `substrate/traffic_simulator/clients.py`, `substrate/traffic_simulator/seeding.py`
- Create: `tests/substrate/traffic_simulator/test_seeding.py`

**Interfaces:**
- Consumes: `config.settings`, `scenarios.Scenario`
- Produces:
  - `clients.SubstrateClients` — Protocol with `list_campaigns() -> list[dict[str, Any]]`, `create_campaign(payload) -> dict[str, Any]`, `add_creative(campaign_id, payload) -> dict[str, Any]`, `patch_campaign(campaign_id, payload) -> None`, `ad_request(payload) -> tuple[int, dict[str, Any]]`, `report_event(payload) -> int`
  - `clients.HttpSubstrateClients` — the httpx implementation of that protocol
  - `clients.build_clients() -> SubstrateClients` — the FastAPI dependency, overridden in tests
  - `seeding.SEED_CAMPAIGNS: list[dict[str, Any]]` — three realistic campaigns with creatives
  - `seeding.seed_if_empty(clients: SubstrateClients) -> int` — returns how many campaigns it created
  - `seeding.apply_mutation(clients: SubstrateClients, mutation: str) -> int` — returns how many campaigns it changed

`ad_request` returns the status code alongside the body so the driver can count a 422 without an exception; a malformed request is an expected outcome of `error_burst`, not an error in the simulator.

- [ ] **Step 1: Write the failing seeding tests**

Create `tests/substrate/traffic_simulator/test_seeding.py`:

```python
"""Seeding a realistic campaign set, and mutating it to inject a failure."""

from __future__ import annotations

from typing import Any

from substrate.traffic_simulator.seeding import SEED_CAMPAIGNS, apply_mutation, seed_if_empty


class FakeClients:
    """Records what the simulator would have done to campaign-service."""

    def __init__(self, campaigns: list[dict[str, Any]] | None = None) -> None:
        self.campaigns = campaigns or []
        self.created: list[dict[str, Any]] = []
        self.creatives: list[tuple[str, dict[str, Any]]] = []
        self.patches: list[tuple[str, dict[str, Any]]] = []

    def list_campaigns(self) -> list[dict[str, Any]]:
        return self.campaigns

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        created = dict(payload, id=f"campaign-{len(self.created)}")
        self.created.append(created)
        self.campaigns.append(created)
        return created

    def add_creative(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.creatives.append((campaign_id, payload))
        return dict(payload, id="creative-1", campaign_id=campaign_id)

    def patch_campaign(self, campaign_id: str, payload: dict[str, Any]) -> None:
        self.patches.append((campaign_id, payload))


def test_the_seed_set_is_three_campaigns_each_with_a_creative() -> None:
    assert len(SEED_CAMPAIGNS) == 3
    for entry in SEED_CAMPAIGNS:
        assert entry["campaign"]["status"] == "active"
        assert entry["creatives"]


def test_seeding_an_empty_platform_creates_the_whole_set() -> None:
    clients = FakeClients()
    created = seed_if_empty(clients)
    assert created == 3
    assert len(clients.created) == 3
    assert len(clients.creatives) >= 3


def test_seeding_is_a_no_op_when_campaigns_already_exist() -> None:
    clients = FakeClients(campaigns=[{"id": "existing"}])
    assert seed_if_empty(clients) == 0
    assert clients.created == []


def test_a_bad_config_deploy_retargets_every_campaign() -> None:
    clients = FakeClients(campaigns=[{"id": "a"}, {"id": "b"}])
    changed = apply_mutation(clients, "retarget_all_campaigns")
    assert changed == 2
    assert {campaign_id for campaign_id, _ in clients.patches} == {"a", "b"}
    for _, payload in clients.patches:
        # Antarctica has no traffic, which is the point: nothing can match.
        assert payload["targeting"]["countries"] == ["AQ"]


def test_a_budget_runaway_inflates_exactly_one_campaign() -> None:
    clients = FakeClients(campaigns=[{"id": "a", "daily_budget_micros": 50_000_000}, {"id": "b"}])
    changed = apply_mutation(clients, "inflate_one_daily_budget")
    assert changed == 1
    campaign_id, payload = clients.patches[0]
    assert campaign_id == "a"
    assert payload["daily_budget_micros"] > 50_000_000


def test_an_unknown_mutation_changes_nothing() -> None:
    clients = FakeClients(campaigns=[{"id": "a"}])
    assert apply_mutation(clients, "not_a_mutation") == 0
    assert clients.patches == []


def test_mutations_are_skipped_when_there_are_no_campaigns() -> None:
    clients = FakeClients()
    assert apply_mutation(clients, "retarget_all_campaigns") == 0
    assert apply_mutation(clients, "inflate_one_daily_budget") == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m uv run pytest tests/substrate/traffic_simulator/test_seeding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'substrate.traffic_simulator.seeding'`

- [ ] **Step 3: Implement the clients**

Create `substrate/traffic_simulator/clients.py`:

```python
"""HTTP access to the three substrate services, behind one narrow protocol.

The driver never imports httpx. It takes this protocol, which is what lets the
whole simulation be exercised in tests without opening a socket.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from substrate.traffic_simulator.config import settings


class SubstrateClients(Protocol):
    """Everything the simulator needs from the rest of the substrate."""

    def list_campaigns(self) -> list[dict[str, Any]]:
        """Every campaign campaign-service knows about."""
        ...

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Open a campaign and return it as created."""
        ...

    def add_creative(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach an ad asset to a campaign."""
        ...

    def patch_campaign(self, campaign_id: str, payload: dict[str, Any]) -> None:
        """Apply a partial update — this is how a failure mode gets injected."""
        ...

    def ad_request(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Ask for an ad. Returns the status code with the body: a 422 is data, not an error."""
        ...

    def report_event(self, payload: dict[str, Any]) -> int:
        """Report an impression or click; returns the status code."""
        ...


class HttpSubstrateClients:
    """The real thing: three httpx clients, one per service."""

    def __init__(
        self,
        campaign_service_url: str,
        ad_decision_url: str,
        event_service_url: str,
        timeout_seconds: float,
    ) -> None:
        self._campaigns = httpx.Client(base_url=campaign_service_url, timeout=timeout_seconds)
        self._decisions = httpx.Client(base_url=ad_decision_url, timeout=timeout_seconds)
        self._events = httpx.Client(base_url=event_service_url, timeout=timeout_seconds)

    def list_campaigns(self) -> list[dict[str, Any]]:
        """Every campaign campaign-service knows about."""
        response = self._campaigns.get("/campaigns")
        response.raise_for_status()
        campaigns: list[dict[str, Any]] = response.json()
        return campaigns

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Open a campaign and return it as created."""
        response = self._campaigns.post("/campaigns", json=payload)
        response.raise_for_status()
        campaign: dict[str, Any] = response.json()
        return campaign

    def add_creative(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach an ad asset to a campaign."""
        response = self._campaigns.post(f"/campaigns/{campaign_id}/creatives", json=payload)
        response.raise_for_status()
        creative: dict[str, Any] = response.json()
        return creative

    def patch_campaign(self, campaign_id: str, payload: dict[str, Any]) -> None:
        """Apply a partial update — this is how a failure mode gets injected."""
        response = self._campaigns.patch(f"/campaigns/{campaign_id}", json=payload)
        response.raise_for_status()

    def ad_request(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Ask for an ad. Returns the status code with the body: a 422 is data, not an error."""
        response = self._decisions.post("/ad-request", json=payload)
        body: dict[str, Any] = response.json()
        return response.status_code, body

    def report_event(self, payload: dict[str, Any]) -> int:
        """Report an impression or click; returns the status code."""
        return self._events.post("/events", json=payload).status_code

    def close(self) -> None:
        """Release all three connection pools."""
        self._campaigns.close()
        self._decisions.close()
        self._events.close()


_clients = HttpSubstrateClients(
    settings.campaign_service_url,
    settings.ad_decision_url,
    settings.event_service_url,
    settings.request_timeout_seconds,
)


def build_clients() -> SubstrateClients:
    """FastAPI dependency returning the process-wide clients. Overridden in tests."""
    return _clients
```

- [ ] **Step 4: Implement seeding**

Create `substrate/traffic_simulator/seeding.py`:

```python
"""A realistic campaign set, and the config mutations that break it.

Both halves talk to campaign-service through its public API. Nothing here reaches
into a database, and nothing fakes a symptom: a bad config deploy really does
deploy a bad config (ADR-0005).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from substrate.traffic_simulator.clients import SubstrateClients

# Somewhere with no traffic. Retargeting every campaign here is a plausible
# fat-fingered deploy, and it makes fill rate fall off a cliff within seconds.
DEAD_COUNTRY = "AQ"
RUNAWAY_MULTIPLIER = 1_000


def _flight() -> tuple[str, str]:
    """A flight window that is open now and stays open for a month."""
    start = datetime.now(UTC) - timedelta(days=1)
    return start.isoformat(), (start + timedelta(days=30)).isoformat()


def _campaign(
    name: str,
    advertiser: str,
    daily_budget_micros: int,
    frequency_cap_per_day: int,
    countries: list[str],
    device_types: list[str],
    content_ratings: list[str],
    exclusions: list[str],
) -> dict[str, Any]:
    """One campaign body for campaign-service."""
    starts_at, ends_at = _flight()
    return {
        "name": name,
        "advertiser": advertiser,
        "status": "active",
        "budget_micros": daily_budget_micros * 30,
        "daily_budget_micros": daily_budget_micros,
        "frequency_cap_per_day": frequency_cap_per_day,
        "targeting": {
            "countries": countries,
            "device_types": device_types,
            "content_ratings": content_ratings,
        },
        "brand_safety_exclusions": exclusions,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


SEED_CAMPAIGNS: list[dict[str, Any]] = [
    {
        "campaign": _campaign(
            "Wide-reach snack launch",
            "Acme Snacks",
            daily_budget_micros=50_000_000,
            frequency_cap_per_day=3,
            countries=["US", "CA"],
            device_types=["tv", "mobile", "tablet", "desktop"],
            content_ratings=["TV-G", "TV-14"],
            exclusions=["news", "true-crime"],
        ),
        "creatives": [
            {"name": "30s hero spot", "duration_seconds": 30, "asset_url": "https://cdn.example/snack-30.mp4"},
            {"name": "15s cutdown", "duration_seconds": 15, "asset_url": "https://cdn.example/snack-15.mp4"},
        ],
    },
    {
        "campaign": _campaign(
            "Premium sedan, connected TV only",
            "Northwind Motors",
            daily_budget_micros=120_000_000,
            frequency_cap_per_day=2,
            countries=["US", "CA", "GB", "DE"],
            device_types=["tv"],
            content_ratings=["TV-14", "TV-MA"],
            exclusions=["true-crime"],
        ),
        "creatives": [
            {"name": "60s cinematic", "duration_seconds": 60, "asset_url": "https://cdn.example/sedan-60.mp4"},
            {"name": "30s cutdown", "duration_seconds": 30, "asset_url": "https://cdn.example/sedan-30.mp4"},
        ],
    },
    {
        "campaign": _campaign(
            "Sports drink, mobile takeover",
            "Vertex Hydration",
            daily_budget_micros=20_000_000,
            frequency_cap_per_day=5,
            countries=["US", "BR", "GB"],
            device_types=["mobile", "tablet"],
            content_ratings=["TV-G", "TV-14", "TV-MA"],
            exclusions=[],
        ),
        "creatives": [
            {"name": "15s bumper", "duration_seconds": 15, "asset_url": "https://cdn.example/drink-15.mp4"},
        ],
    },
]


def seed_if_empty(clients: SubstrateClients) -> int:
    """Create the seed campaign set, unless the platform already has campaigns."""
    if clients.list_campaigns():
        return 0
    created = 0
    for entry in SEED_CAMPAIGNS:
        campaign = clients.create_campaign(entry["campaign"])
        for creative in entry["creatives"]:
            clients.add_creative(str(campaign["id"]), creative)
        created += 1
    return created


def apply_mutation(clients: SubstrateClients, mutation: str) -> int:
    """Inject a configuration failure. Returns how many campaigns were changed."""
    campaigns = clients.list_campaigns()
    if not campaigns:
        return 0

    if mutation == "retarget_all_campaigns":
        for campaign in campaigns:
            clients.patch_campaign(
                str(campaign["id"]),
                {"targeting": {"countries": [DEAD_COUNTRY], "device_types": [], "content_ratings": []}},
            )
        return len(campaigns)

    if mutation == "inflate_one_daily_budget":
        target = campaigns[0]
        current = int(target.get("daily_budget_micros") or 1_000_000)
        clients.patch_campaign(
            str(target["id"]),
            {
                "daily_budget_micros": current * RUNAWAY_MULTIPLIER,
                "budget_micros": current * RUNAWAY_MULTIPLIER * 30,
                "frequency_cap_per_day": 50,
            },
        )
        return 1

    return 0
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m uv run pytest tests/substrate/traffic_simulator -v`
Expected: PASS (21 tests)

- [ ] **Step 6: Commit**

```bash
git add substrate/traffic_simulator tests/substrate/traffic_simulator
git commit -m "feat: substrate client protocol, seed campaign set, and config-mutating failure injection"
```

---

### Task 3: The driver

**Files:**
- Create: `substrate/traffic_simulator/driver.py`, `tests/substrate/traffic_simulator/test_driver.py`

**Interfaces:**
- Consumes: `clients.SubstrateClients`, `population.Population`, `scenarios.Scenario`, `scenarios.corrupt`
- Produces:
  - `driver.TickResult` — frozen dataclass: `status_code: int`, `filled: bool`, `no_fill_reason: str | None`, `events_reported: int`
  - `driver.tick(clients, population, scenario, random_source, click_probability) -> TickResult`

One tick is the whole loop, and it is synchronous and pure of timing:

1. Build an ad request from the population.
2. With probability `scenario.malformed_fraction`, corrupt it.
3. `POST /ad-request`. A non-200 ends the tick — that is the `error_burst` signal.
4. If the decision filled, report an **impression** to event-service, carrying the decision's `request_id` so the event ties back to the decision that produced it.
5. With probability `click_probability`, report a **click** for the same impression.

- [ ] **Step 1: Write the failing driver tests**

Create `tests/substrate/traffic_simulator/test_driver.py`:

```python
"""One tick of the simulation loop, with no network and no clock."""

from __future__ import annotations

import random
from typing import Any

from substrate.traffic_simulator.driver import tick
from substrate.traffic_simulator.population import Population
from substrate.traffic_simulator.scenarios import get

FILLED_DECISION: dict[str, Any] = {
    "request_id": "44444444-4444-4444-4444-444444444444",
    "slot_id": "slot-1",
    "filled": True,
    "ad": {
        "campaign_id": "11111111-1111-1111-1111-111111111111",
        "campaign_name": "Wide-reach snack launch",
        "advertiser": "Acme Snacks",
        "creative_id": "22222222-2222-2222-2222-222222222222",
        "creative_name": "30s hero spot",
        "asset_url": "https://cdn.example/snack-30.mp4",
        "duration_seconds": 30,
        "price_micros": 2_000,
    },
    "no_fill_reason": None,
    "candidates_considered": 3,
    "trace": [],
    "decision_latency_ms": 1.2,
}

NO_FILL_DECISION: dict[str, Any] = {
    "request_id": "55555555-5555-5555-5555-555555555555",
    "slot_id": "slot-2",
    "filled": False,
    "ad": None,
    "no_fill_reason": "targeting_mismatch",
    "candidates_considered": 3,
    "trace": [],
    "decision_latency_ms": 0.9,
}


class RecordingClients:
    """Answers ad requests with a canned decision and records reported events."""

    def __init__(self, status: int = 200, decision: dict[str, Any] | None = None) -> None:
        self.status = status
        self.decision = decision if decision is not None else FILLED_DECISION
        self.requests: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def ad_request(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.requests.append(payload)
        return self.status, self.decision

    def report_event(self, payload: dict[str, Any]) -> int:
        self.events.append(payload)
        return 201

    def list_campaigns(self) -> list[dict[str, Any]]:
        return []

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    def add_creative(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    def patch_campaign(self, campaign_id: str, payload: dict[str, Any]) -> None:
        return None


def run(clients: RecordingClients, scenario_name: str = "steady", click_probability: float = 0.0):
    """Run one tick with a fixed RNG so probabilities are decidable."""
    return tick(
        clients,
        Population(seed=1),
        get(scenario_name),
        random_source=random.Random(99),
        click_probability=click_probability,
    )


def test_a_fill_reports_exactly_one_impression() -> None:
    clients = RecordingClients()
    result = run(clients)

    assert result.filled is True
    assert result.status_code == 200
    assert result.events_reported == 1
    assert clients.events[0]["event_type"] == "impression"
    assert clients.events[0]["price_micros"] == 2_000


def test_the_impression_ties_back_to_the_decision_that_produced_it() -> None:
    clients = RecordingClients()
    run(clients)

    event = clients.events[0]
    assert event["request_id"] == FILLED_DECISION["request_id"]
    assert event["campaign_id"] == FILLED_DECISION["ad"]["campaign_id"]
    assert event["creative_id"] == FILLED_DECISION["ad"]["creative_id"]
    assert event["slot_id"] == FILLED_DECISION["slot_id"]


def test_every_event_carries_its_own_idempotency_key() -> None:
    clients = RecordingClients()
    run(clients, click_probability=1.0)

    ids = [event["event_id"] for event in clients.events]
    assert len(ids) == len(set(ids)) == 2


def test_a_certain_click_reports_a_second_event_for_the_same_impression() -> None:
    clients = RecordingClients()
    result = run(clients, click_probability=1.0)

    assert result.events_reported == 2
    assert [event["event_type"] for event in clients.events] == ["impression", "click"]
    assert clients.events[1]["price_micros"] == 0
    assert clients.events[1]["request_id"] == clients.events[0]["request_id"]


def test_a_no_fill_reports_nothing_and_keeps_the_reason() -> None:
    clients = RecordingClients(decision=NO_FILL_DECISION)
    result = run(clients, click_probability=1.0)

    assert result.filled is False
    assert result.no_fill_reason == "targeting_mismatch"
    assert result.events_reported == 0
    assert clients.events == []


def test_a_rejected_request_ends_the_tick_without_reporting() -> None:
    clients = RecordingClients(status=422, decision={"error": {"code": 422, "message": "bad"}})
    result = run(clients, click_probability=1.0)

    assert result.status_code == 422
    assert result.filled is False
    assert result.events_reported == 0
    assert clients.events == []


def test_error_burst_sends_requests_the_api_must_reject() -> None:
    clients = RecordingClients(status=422, decision={"error": {"code": 422, "message": "bad"}})
    for _ in range(20):
        tick(
            clients,
            Population(seed=1),
            get("error_burst"),
            random_source=random.Random(1),
            click_probability=0.0,
        )

    corrupted = [
        request for request in clients.requests if request["slot"]["duration_seconds"] == 0
    ]
    assert corrupted, "error_burst never corrupted a request"


def test_steady_never_corrupts_a_request() -> None:
    clients = RecordingClients()
    for _ in range(20):
        tick(
            clients,
            Population(seed=2),
            get("steady"),
            random_source=random.Random(3),
            click_probability=0.0,
        )

    assert all(request["slot"]["duration_seconds"] > 0 for request in clients.requests)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m uv run pytest tests/substrate/traffic_simulator/test_driver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'substrate.traffic_simulator.driver'`

- [ ] **Step 3: Implement the driver**

Create `substrate/traffic_simulator/driver.py`:

```python
"""One tick of the simulation: request an ad, then report what happened.

No sleeping, no clock, no network of its own — the loop that calls this owns the
timing and the control plane owns the clients. That is what makes the interesting
part (what gets reported, and when) testable without infrastructure.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from substrate.traffic_simulator.clients import SubstrateClients
from substrate.traffic_simulator.population import Population
from substrate.traffic_simulator.scenarios import Scenario, corrupt


@dataclass(frozen=True)
class TickResult:
    """What one ad request turned into."""

    status_code: int
    filled: bool
    no_fill_reason: str | None
    events_reported: int


def _event(
    event_type: str, decision: dict[str, Any], ad: dict[str, Any], price_micros: int
) -> dict[str, Any]:
    """A delivery report for one decision, with its own idempotency key."""
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "request_id": decision["request_id"],
        "campaign_id": ad["campaign_id"],
        "creative_id": ad["creative_id"],
        "member_id": decision.get("member_id", "member-unknown"),
        "slot_id": decision["slot_id"],
        "price_micros": price_micros,
        "occurred_at": datetime.now(UTC).isoformat(),
    }


def tick(
    clients: SubstrateClients,
    population: Population,
    scenario: Scenario,
    random_source: random.Random,
    click_probability: float,
) -> TickResult:
    """Run one ad request end to end and report the delivery it produced."""
    request = population.ad_request()
    if random_source.random() < scenario.malformed_fraction:
        request = corrupt(request)

    member = request["member"]
    member_id = member["member_id"] if isinstance(member, dict) else "member-unknown"

    status_code, decision = clients.ad_request(request)
    if status_code != 200:
        return TickResult(status_code, filled=False, no_fill_reason=None, events_reported=0)

    ad = decision.get("ad")
    if not decision.get("filled") or not isinstance(ad, dict):
        return TickResult(
            status_code,
            filled=False,
            no_fill_reason=decision.get("no_fill_reason"),
            events_reported=0,
        )

    decision = {**decision, "member_id": member_id}
    reported = 0
    clients.report_event(_event("impression", decision, ad, int(ad["price_micros"])))
    reported += 1
    if random_source.random() < click_probability:
        clients.report_event(_event("click", decision, ad, 0))
        reported += 1

    return TickResult(status_code, filled=True, no_fill_reason=None, events_reported=reported)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m uv run pytest tests/substrate/traffic_simulator -v`
Expected: PASS (29 tests)

- [ ] **Step 5: Commit**

```bash
git add substrate/traffic_simulator/driver.py tests/substrate/traffic_simulator/test_driver.py
git commit -m "feat: simulation driver — ad request to delivery report, with no I/O of its own"
```

---

### Task 4: The control plane

**Files:**
- Create: `substrate/traffic_simulator/main.py`, `tests/substrate/traffic_simulator/conftest.py`, `test_api.py`

**Interfaces:**
- Produces `app: FastAPI` exposing:

| Method | Path | Success | Failure |
|---|---|---|---|
| GET | `/health` | 200 `{"status": "ok", "service": "traffic-simulator"}` | — |
| GET | `/scenarios` | 200 `list[ScenarioRead]` — name, summary, and the three knobs | — |
| GET | `/status` | 200 `SimulatorStatus` — running, active scenario, ticks, fills, no-fills, errors, events | — |
| POST | `/scenario` | 200 `SimulatorStatus`, applying any config mutation the scenario names | 404 `ErrorResponse` for an unknown scenario; 422 on an invalid body |
| POST | `/control` | 200 `SimulatorStatus` — `{"running": true|false}` | 422 `ErrorResponse` |
| POST | `/seed` | 200 `{"created": n}` | — |
| GET | `/metrics` | 200 Prometheus exposition | — |

- Also produces `state: SimulatorState` (a module-level singleton holding counters, the active scenario, and the running flag) and `get_state()` / `build_clients()` dependency seams.
- Metrics: the shared request pair, plus `sim_ad_requests_total{service,outcome}` (`filled` | `no_fill` | `rejected`), `sim_events_reported_total{service,event_type}`, and `sim_scenario_info{service,scenario}` (a Gauge set to 1 for the active scenario and 0 for the rest).
- Switching scenario emits one structured log line with `service`, `endpoint`, `scenario`, `config_mutation`, `campaigns_changed` — the line the Level 3 log-intelligence pipeline will correlate an incident to.

- [ ] **Step 1: Write the conftest**

Create `tests/substrate/traffic_simulator/conftest.py`:

```python
"""Fixtures backing traffic-simulator tests. No sockets, no background loop."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from substrate.traffic_simulator.clients import build_clients
from substrate.traffic_simulator.main import app, get_state


class StubClients:
    """A whole substrate, in a dict."""

    def __init__(self) -> None:
        self.campaigns: list[dict[str, Any]] = []
        self.patches: list[tuple[str, dict[str, Any]]] = []
        self.events: list[dict[str, Any]] = []

    def list_campaigns(self) -> list[dict[str, Any]]:
        return self.campaigns

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        created = dict(payload, id=f"campaign-{len(self.campaigns)}")
        self.campaigns.append(created)
        return created

    def add_creative(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(payload, id="creative-1", campaign_id=campaign_id)

    def patch_campaign(self, campaign_id: str, payload: dict[str, Any]) -> None:
        self.patches.append((campaign_id, payload))

    def ad_request(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {"request_id": "x", "slot_id": "s", "filled": False, "ad": None}

    def report_event(self, payload: dict[str, Any]) -> int:
        self.events.append(payload)
        return 201


@pytest.fixture
def stub_clients() -> StubClients:
    """The substrate the simulator thinks it is driving."""
    return StubClients()


@pytest.fixture
def client(stub_clients: StubClients) -> Iterator[TestClient]:
    """A TestClient with fresh state and a stubbed substrate.

    Built without entering its context manager on purpose: the lifespan would start
    the background traffic loop and open real connections.
    """
    from substrate.traffic_simulator.main import SimulatorState

    state = SimulatorState()
    app.dependency_overrides[build_clients] = lambda: stub_clients
    app.dependency_overrides[get_state] = lambda: state
    yield TestClient(app)
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Write the failing API tests**

Create `tests/substrate/traffic_simulator/test_api.py`:

```python
"""The control plane: what is running, which failure is injected, and what it did."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.substrate.traffic_simulator.conftest import StubClients


def test_health_reports_the_service_name(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "traffic-simulator"}


def test_the_scenario_catalogue_is_self_describing(client: TestClient) -> None:
    scenarios = client.get("/scenarios").json()
    assert {entry["name"] for entry in scenarios} == {
        "steady",
        "error_burst",
        "traffic_surge",
        "bad_config_deploy",
        "budget_runaway",
    }
    for entry in scenarios:
        assert entry["summary"]


def test_the_simulator_starts_on_the_steady_scenario(client: TestClient) -> None:
    status = client.get("/status").json()
    assert status["scenario"] == "steady"
    assert status["ticks"] == 0


def test_switching_to_a_config_scenario_actually_patches_campaigns(
    client: TestClient, stub_clients: StubClients
) -> None:
    stub_clients.campaigns = [{"id": "a"}, {"id": "b"}]

    response = client.post("/scenario", json={"name": "bad_config_deploy"})
    assert response.status_code == 200
    assert response.json()["scenario"] == "bad_config_deploy"
    assert response.json()["campaigns_changed"] == 2
    assert {campaign_id for campaign_id, _ in stub_clients.patches} == {"a", "b"}


def test_switching_to_a_traffic_scenario_touches_no_configuration(
    client: TestClient, stub_clients: StubClients
) -> None:
    stub_clients.campaigns = [{"id": "a"}]

    response = client.post("/scenario", json={"name": "traffic_surge"})
    assert response.status_code == 200
    assert response.json()["campaigns_changed"] == 0
    assert stub_clients.patches == []


def test_an_unknown_scenario_is_a_typed_404(client: TestClient) -> None:
    response = client.post("/scenario", json={"name": "chaos_monkey"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == 404
    assert "chaos_monkey" in response.json()["error"]["message"]


def test_traffic_can_be_paused_and_resumed(client: TestClient) -> None:
    assert client.post("/control", json={"running": False}).json()["running"] is False
    assert client.post("/control", json={"running": True}).json()["running"] is True


def test_seeding_creates_the_campaign_set_once(
    client: TestClient, stub_clients: StubClients
) -> None:
    assert client.post("/seed").json()["created"] == 3
    assert client.post("/seed").json()["created"] == 0
    assert len(stub_clients.campaigns) == 3


def test_metrics_expose_the_simulator_counters(client: TestClient) -> None:
    client.post("/scenario", json={"name": "error_burst"})
    body = client.get("/metrics").text
    assert "sim_ad_requests_total" in body
    assert "sim_events_reported_total" in body
    assert 'sim_scenario_info{scenario="error_burst"' in body.replace(
        'service="traffic-simulator",', ""
    )
    assert "http_requests_total" in body
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m uv run pytest tests/substrate/traffic_simulator/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'substrate.traffic_simulator.main'`

- [ ] **Step 4: Implement the control plane**

Create `substrate/traffic_simulator/main.py`:

```python
"""traffic-simulator control plane.

Load generation with a switch on it. The background loop drives real ad requests
through the substrate; the API decides how fast, how broken, and which failure is
currently injected — which is what makes the Level 3 demo loop possible: inject a
failure here, watch it appear in Grafana, hand it to an ops agent.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel, Field

from substrate.shared.logging import configure_logging, log_context
from substrate.shared.observability import install_request_observability
from substrate.traffic_simulator.clients import SubstrateClients, build_clients
from substrate.traffic_simulator.config import settings
from substrate.traffic_simulator.driver import tick
from substrate.traffic_simulator.population import Population
from substrate.traffic_simulator.scenarios import SCENARIOS, Scenario
from substrate.traffic_simulator.seeding import apply_mutation, seed_if_empty

logger = logging.getLogger("traffic_simulator.control")

AD_REQUESTS = Counter(
    "sim_ad_requests_total",
    "Ad requests the simulator issued, by outcome.",
    ["service", "outcome"],
)
EVENTS_REPORTED = Counter(
    "sim_events_reported_total",
    "Delivery events the simulator reported, by type.",
    ["service", "event_type"],
)
SCENARIO_INFO = Gauge(
    "sim_scenario_info",
    "1 for the scenario currently injected, 0 for every other.",
    ["service", "scenario"],
)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"description": "Unknown scenario"},
}


class ErrorDetail(BaseModel):
    """The body of a failed request."""

    code: int
    message: str


class ErrorResponse(BaseModel):
    """Every non-2xx response uses this shape."""

    error: ErrorDetail


class ScenarioRead(BaseModel):
    """One injectable failure mode, as the API describes it."""

    name: str
    summary: str
    rate_multiplier: float
    malformed_fraction: float
    config_mutation: str | None


class ScenarioSelect(BaseModel):
    """Which failure mode to inject."""

    name: str = Field(min_length=1, max_length=64)


class ControlRequest(BaseModel):
    """Start or stop generating traffic."""

    running: bool


class SimulatorStatus(BaseModel):
    """What the simulator is doing right now, and what it has done so far."""

    running: bool
    scenario: str
    requests_per_second: float
    ticks: int
    fills: int
    no_fills: int
    rejected: int
    events_reported: int
    campaigns_changed: int = 0


class SimulatorState:
    """Mutable simulation state. One per process, replaced wholesale in tests."""

    def __init__(self) -> None:
        self.running = settings.autostart
        self.scenario: Scenario = SCENARIOS["steady"]
        self.population = Population(settings.seed)
        self.random = random.Random(settings.seed)
        self.ticks = 0
        self.fills = 0
        self.no_fills = 0
        self.rejected = 0
        self.events_reported = 0
        self.campaigns_changed = 0

    def snapshot(self) -> SimulatorStatus:
        """The current state as an API response."""
        return SimulatorStatus(
            running=self.running,
            scenario=self.scenario.name,
            requests_per_second=settings.requests_per_second * self.scenario.rate_multiplier,
            ticks=self.ticks,
            fills=self.fills,
            no_fills=self.no_fills,
            rejected=self.rejected,
            events_reported=self.events_reported,
            campaigns_changed=self.campaigns_changed,
        )


_state = SimulatorState()


def get_state() -> SimulatorState:
    """FastAPI dependency returning simulation state. Overridden in tests."""
    return _state


def _publish_scenario(active: str) -> None:
    """Set the active-scenario gauge to 1 and every other to 0."""
    for name in SCENARIOS:
        SCENARIO_INFO.labels(settings.service_name, name).set(1 if name == active else 0)


def _run_one_tick(state: SimulatorState, clients: SubstrateClients) -> None:
    """Drive one ad request and fold the result into state and metrics."""
    result = tick(
        clients,
        state.population,
        state.scenario,
        random_source=state.random,
        click_probability=settings.click_probability,
    )
    state.ticks += 1
    state.events_reported += result.events_reported

    if result.status_code != 200:
        state.rejected += 1
        AD_REQUESTS.labels(settings.service_name, "rejected").inc()
    elif result.filled:
        state.fills += 1
        AD_REQUESTS.labels(settings.service_name, "filled").inc()
        EVENTS_REPORTED.labels(settings.service_name, "impression").inc()
        if result.events_reported > 1:
            EVENTS_REPORTED.labels(settings.service_name, "click").inc()
    else:
        state.no_fills += 1
        AD_REQUESTS.labels(settings.service_name, "no_fill").inc()


async def _traffic_loop() -> None:
    """Generate traffic forever at the active scenario's rate."""
    clients = build_clients()
    with suppress(Exception):
        seeded = await asyncio.to_thread(seed_if_empty, clients)
        if seeded:
            log_context(logger, "seeded campaigns", service=settings.service_name, created=seeded)

    while True:
        state = get_state()
        rate = max(settings.requests_per_second * state.scenario.rate_multiplier, 0.1)
        await asyncio.sleep(1.0 / rate)
        if not state.running:
            continue
        # A substrate that is down is a condition to keep driving through, not to
        # die on — the whole point is to still be generating load during an incident.
        with suppress(Exception):
            await asyncio.to_thread(_run_one_tick, state, clients)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure logging, publish the starting scenario, and start the traffic loop."""
    configure_logging(settings.service_name)
    _publish_scenario("steady")
    task = asyncio.create_task(_traffic_loop())
    logger.info("traffic-simulator ready")
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(
    title="traffic-simulator",
    version="0.1.0",
    summary="Seeded ad-request load with five injectable failure modes.",
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


@app.get("/scenarios", response_model=list[ScenarioRead])
def list_scenarios() -> list[ScenarioRead]:
    """Every failure mode this simulator can inject, and what each one does."""
    return [ScenarioRead.model_validate(vars(scenario)) for scenario in SCENARIOS.values()]


@app.get("/status", response_model=SimulatorStatus)
def read_status(state: SimulatorState = Depends(get_state)) -> SimulatorStatus:
    """What the simulator is doing right now."""
    return state.snapshot()


@app.post("/scenario", response_model=SimulatorStatus, responses=ERROR_RESPONSES)
def select_scenario(
    payload: ScenarioSelect,
    state: SimulatorState = Depends(get_state),
    clients: SubstrateClients = Depends(build_clients),
) -> SimulatorStatus:
    """Inject a failure mode, applying any configuration change it calls for."""
    scenario = SCENARIOS.get(payload.name)
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown scenario {payload.name!r}; see GET /scenarios",
        )

    changed = apply_mutation(clients, scenario.config_mutation) if scenario.config_mutation else 0
    state.scenario = scenario
    state.campaigns_changed = changed
    _publish_scenario(scenario.name)

    log_context(
        logger,
        "scenario injected",
        service=settings.service_name,
        endpoint="/scenario",
        scenario=scenario.name,
        config_mutation=scenario.config_mutation,
        campaigns_changed=changed,
    )
    return state.snapshot()


@app.post("/control", response_model=SimulatorStatus)
def control(
    payload: ControlRequest, state: SimulatorState = Depends(get_state)
) -> SimulatorStatus:
    """Start or stop generating traffic without restarting the container."""
    state.running = payload.running
    log_context(
        logger,
        "traffic toggled",
        service=settings.service_name,
        endpoint="/control",
        running=state.running,
    )
    return state.snapshot()


@app.post("/seed")
def seed(clients: SubstrateClients = Depends(build_clients)) -> dict[str, int]:
    """Create the seed campaign set, unless the platform already has campaigns."""
    return {"created": seed_if_empty(clients)}
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m uv run pytest tests/substrate/traffic_simulator -v`
Expected: PASS (38 tests)

- [ ] **Step 6: Verify the whole suite is hermetic and clean, gates unpiped**

```bash
python -m uv run pytest
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate
```

Expected: all green, with Docker stopped.

- [ ] **Step 7: Commit**

```bash
git add substrate/traffic_simulator tests/substrate/traffic_simulator
git commit -m "feat: traffic-simulator control plane with live scenario switching"
```

---

### Task 5: Containerize, wire in, and dashboard the simulator

**Files:**
- Create: `substrate/traffic_simulator/Dockerfile`
- Modify: `docker-compose.yml`, `infra/prometheus/prometheus.yml`, `infra/grafana/provisioning/dashboards/ads-delivery.json`, `tests/infra/test_grafana_dashboards.py`, `README.md`

**Interfaces:**
- Produces: a `traffic-simulator` container on host port **8004**, depending on healthy campaign-service, ad-decision-service, and event-service; scraped by Prometheus; one new dashboard panel showing the injected scenario.

- [ ] **Step 1: Write the Dockerfile**

Create `substrate/traffic_simulator/Dockerfile`:

```dockerfile
# traffic-simulator: seeded ad-request load with injectable failure modes.
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

CMD ["uvicorn", "substrate.traffic_simulator.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Add the service to Compose**

In `docker-compose.yml`, after the `event-service` block:

```yaml
  traffic-simulator:
    build:
      context: .
      dockerfile: substrate/traffic_simulator/Dockerfile
    environment:
      SIM_CAMPAIGN_SERVICE_URL: http://campaign-service:8000
      SIM_AD_DECISION_URL: http://ad-decision-service:8000
      SIM_EVENT_SERVICE_URL: http://event-service:8000
    ports: ["8004:8000"]
    depends_on:
      campaign-service:
        condition: service_healthy
      ad-decision-service:
        condition: service_healthy
      event-service:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\""]
      interval: 10s
      timeout: 3s
      retries: 5
```

- [ ] **Step 3: Register the Prometheus scrape target**

In `infra/prometheus/prometheus.yml`, below the event-service job:

```yaml
  - job_name: traffic-simulator
    static_configs:
      - targets: ["traffic-simulator:8000"]
```

Change the trailing comment to `# Every Level 0 substrate service is registered. Level 1+ services join here.`

- [ ] **Step 4: Add the injected-scenario panel**

In `infra/grafana/provisioning/dashboards/ads-delivery.json`, add a panel with `"id": 8` at `gridPos {"h": 5, "w": 24, "x": 0, "y": 23}`, type `"state-timeline"`, title `"Injected scenario"`, description `"Which failure mode the traffic-simulator has active. Every anomaly on this dashboard should line up with a band here."`, datasource `{"type": "prometheus", "uid": "prometheus"}`, target:

```json
{ "refId": "A", "expr": "sim_scenario_info > 0", "legendFormat": "{{scenario}}" }
```

with `fieldConfig.defaults.color` `{"mode": "palette-classic"}`, `custom.fillOpacity: 70`, `custom.lineWidth: 0`, and `options.legend {"displayMode": "list", "placement": "bottom", "showLegend": true}`.

Then extend `SUBSTRATE_METRICS` in `tests/infra/test_grafana_dashboards.py` with `"sim_scenario_info"`, `"sim_ad_requests_total"`, and `"sim_events_reported_total"`.

- [ ] **Step 5: Update the README**

Tick `- [x] Day 5 — traffic-simulator + failure injection`, set **Status** to `**Day 5** — Level 0 complete.`, add `| traffic-simulator control API | http://localhost:8004/docs |` to the service table, and add a short **Injecting a failure** section:

```markdown
### Injecting a failure

```bash
curl -s localhost:8004/scenarios | python -m json.tool   # what can be injected
curl -s -X POST localhost:8004/scenario -H 'content-type: application/json' \
  -d '{"name":"bad_config_deploy"}'                      # inject it
curl -s -X POST localhost:8004/scenario -H 'content-type: application/json' \
  -d '{"name":"steady"}'                                 # back to baseline
```

Watch it land on the **ads-delivery** dashboard in Grafana.
```

- [ ] **Step 6: Bring the stack up and verify by hand**

```bash
docker compose up -d --build
docker compose restart prometheus grafana
sleep 30
curl -s localhost:8004/status
curl -s localhost:8003/delivery
```

Expected: `/status` shows a rising `ticks` and non-zero `fills`; `/delivery` shows impressions accumulating against the seeded campaigns; `localhost:9090/targets` shows five substrate targets up.

- [ ] **Step 7: Commit**

```bash
git add substrate/traffic_simulator/Dockerfile docker-compose.yml infra README.md tests/infra
git commit -m "feat: containerize traffic-simulator and chart the injected scenario"
```

---

### Task 6: The Level 0 quality gate

**Files:**
- Create: `platform/gates/__init__.py`, `platform/gates/level0_gate.py`, `platform/gates/README.md`
- Create: `tests/platform/__init__.py`, `tests/platform/test_level0_gate.py`

**Interfaces:**
- Produces:
  - `level0_gate.HEALTH_ENDPOINTS: dict[str, str]` — the five substrate services and their health URLs
  - `level0_gate.GateCheck` — frozen dataclass: `name: str`, `passed: bool`, `detail: str`
  - `level0_gate.evaluate_scenario(before: dict[str, float], after: dict[str, float], scenario: str) -> GateCheck` — pure; decides whether an injected failure actually showed up in the metrics
  - `level0_gate.summarize(checks: list[GateCheck]) -> tuple[int, int]` — passed, total
  - `level0_gate.main() -> int` — runs the gate against the live stack, prints a table, returns an exit code

The gate is the spec's Level 0 metric: **all services healthy under simulator load, and failure injection works.** It checks:

1. All five substrate services answer `/health`
2. All five are `up` in Prometheus
3. Under `steady`, the simulator's tick count rises and fills are non-zero
4. `error_burst` raises rejected requests
5. `bad_config_deploy` drives fill rate down
6. `budget_runaway` changes campaign configuration
7. `traffic_surge` raises the request rate
8. The stack returns to healthy on `steady`

Steps 4–7 are decided by `evaluate_scenario`, which is pure and unit-tested; the live run only supplies the numbers.

- [ ] **Step 1: Write the failing gate-logic tests**

Create `tests/platform/__init__.py` (empty file).

Create `tests/platform/test_level0_gate.py`:

```python
"""The Level 0 gate's decision logic, tested without a running stack."""

from __future__ import annotations

from platform_gates import GateCheck, evaluate_scenario, summarize


def test_an_error_burst_must_raise_rejected_requests() -> None:
    check = evaluate_scenario({"rejected": 0.0}, {"rejected": 12.0}, "error_burst")
    assert check.passed is True
    assert "12" in check.detail


def test_an_error_burst_that_rejects_nothing_fails() -> None:
    assert evaluate_scenario({"rejected": 3.0}, {"rejected": 3.0}, "error_burst").passed is False


def test_a_bad_config_deploy_must_drive_fill_rate_down() -> None:
    before = {"fills": 100.0, "ticks": 120.0}
    after = {"fills": 100.0, "ticks": 220.0}  # 100 more ticks, zero more fills
    assert evaluate_scenario(before, after, "bad_config_deploy").passed is True


def test_a_bad_config_deploy_that_changes_nothing_fails() -> None:
    before = {"fills": 100.0, "ticks": 120.0}
    after = {"fills": 200.0, "ticks": 220.0}  # every tick still fills
    assert evaluate_scenario(before, after, "bad_config_deploy").passed is False


def test_a_budget_runaway_must_have_changed_configuration() -> None:
    assert evaluate_scenario({}, {"campaigns_changed": 1.0}, "budget_runaway").passed is True
    assert evaluate_scenario({}, {"campaigns_changed": 0.0}, "budget_runaway").passed is False


def test_a_traffic_surge_must_raise_the_request_rate() -> None:
    assert (
        evaluate_scenario(
            {"requests_per_second": 5.0}, {"requests_per_second": 50.0}, "traffic_surge"
        ).passed
        is True
    )
    assert (
        evaluate_scenario(
            {"requests_per_second": 5.0}, {"requests_per_second": 5.0}, "traffic_surge"
        ).passed
        is False
    )


def test_steady_must_actually_serve_ads() -> None:
    before = {"ticks": 0.0, "fills": 0.0}
    after = {"ticks": 60.0, "fills": 45.0}
    assert evaluate_scenario(before, after, "steady").passed is True
    assert evaluate_scenario(before, {"ticks": 60.0, "fills": 0.0}, "steady").passed is False


def test_an_unknown_scenario_never_silently_passes() -> None:
    assert evaluate_scenario({}, {}, "not_a_scenario").passed is False


def test_the_summary_counts_passes() -> None:
    checks = [
        GateCheck("a", True, ""),
        GateCheck("b", False, ""),
        GateCheck("c", True, ""),
    ]
    assert summarize(checks) == (2, 3)
```

Note the import is `platform_gates` — `platform` is a Python **standard-library module name**, so the package cannot be imported as `platform.gates` without shadowing it. Add to `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
pythonpath = [".", "platform"]
```

and name the module file `platform/platform_gates.py` rather than `platform/gates/level0_gate.py`. Adjust the Files list at the top of this task accordingly: create `platform/platform_gates.py` and `platform/README.md`.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m uv run pytest tests/platform -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'platform_gates'`

- [ ] **Step 3: Implement the gate**

Create `platform/platform_gates.py`:

```python
"""The Level 0 quality gate.

The spec's Level 0 metric is "all services healthy under simulator load; failure
injection works — 100%". This turns that sentence into a script that either exits
zero or tells you which of the eight checks failed.

The decision logic is pure and unit-tested. The live run only supplies numbers,
which is why a gate can be trusted: it cannot pass by accident when the substrate
is down, and it cannot fail because a test double drifted from reality.
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

HEALTH_ENDPOINTS: dict[str, str] = {
    "campaign-service": "http://localhost:8001/health",
    "ad-decision-service": "http://localhost:8002/health",
    "event-service": "http://localhost:8003/health",
    "traffic-simulator": "http://localhost:8004/health",
    "prometheus": "http://localhost:9090/-/healthy",
}

SIMULATOR = "http://localhost:8004"
SETTLE_SECONDS = 20


@dataclass(frozen=True)
class GateCheck:
    """One gate criterion and whether the live stack met it."""

    name: str
    passed: bool
    detail: str


def evaluate_scenario(
    before: dict[str, float], after: dict[str, float], scenario: str
) -> GateCheck:
    """Decide whether an injected failure actually showed up in the numbers."""
    if scenario == "steady":
        served = after.get("ticks", 0) - before.get("ticks", 0)
        filled = after.get("fills", 0) - before.get("fills", 0)
        return GateCheck(
            "steady serves ads",
            served > 0 and filled > 0,
            f"{int(filled)} fills across {int(served)} requests",
        )

    if scenario == "error_burst":
        rejected = after.get("rejected", 0) - before.get("rejected", 0)
        return GateCheck(
            "error_burst produces rejections",
            rejected > 0,
            f"{int(rejected)} requests rejected",
        )

    if scenario == "traffic_surge":
        return GateCheck(
            "traffic_surge raises the rate",
            after.get("requests_per_second", 0) > before.get("requests_per_second", 0),
            f"{before.get('requests_per_second', 0)} -> {after.get('requests_per_second', 0)} rps",
        )

    if scenario == "bad_config_deploy":
        served = after.get("ticks", 0) - before.get("ticks", 0)
        filled = after.get("fills", 0) - before.get("fills", 0)
        fill_rate = filled / served if served else 1.0
        return GateCheck(
            "bad_config_deploy collapses fill rate",
            served > 0 and fill_rate < 0.2,
            f"fill rate {fill_rate:.0%} across {int(served)} requests",
        )

    if scenario == "budget_runaway":
        changed = after.get("campaigns_changed", 0)
        return GateCheck(
            "budget_runaway changes configuration",
            changed > 0,
            f"{int(changed)} campaign(s) mutated",
        )

    return GateCheck(f"unknown scenario {scenario}", False, "no criterion defined")


def summarize(checks: list[GateCheck]) -> tuple[int, int]:
    """How many checks passed, out of how many."""
    return sum(1 for check in checks if check.passed), len(checks)


def _get(url: str) -> Any:
    """GET a URL and parse the JSON body."""
    import json

    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def _post(url: str, payload: dict[str, Any]) -> Any:
    """POST JSON and parse the response."""
    import json

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def _health_checks() -> list[GateCheck]:
    """Every substrate service answers its health endpoint."""
    checks: list[GateCheck] = []
    for service, url in HEALTH_ENDPOINTS.items():
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                ok = response.status == 200
            checks.append(GateCheck(f"{service} healthy", ok, f"HTTP {response.status}"))
        except (urllib.error.URLError, TimeoutError) as exc:
            checks.append(GateCheck(f"{service} healthy", False, str(exc)))
    return checks


def _targets_check() -> GateCheck:
    """Prometheus is scraping every substrate service."""
    try:
        body = _get("http://localhost:9090/api/v1/targets")
    except (urllib.error.URLError, TimeoutError) as exc:
        return GateCheck("prometheus scrapes all targets", False, str(exc))

    healthy = {
        target["labels"]["job"]
        for target in body["data"]["activeTargets"]
        if target["health"] == "up"
    }
    expected = {
        "campaign-service",
        "ad-decision-service",
        "event-service",
        "traffic-simulator",
    }
    missing = expected - healthy
    return GateCheck(
        "prometheus scrapes all targets",
        not missing,
        "all up" if not missing else f"missing {sorted(missing)}",
    )


def _run_scenario(scenario: str) -> GateCheck:
    """Inject one scenario, let it settle, and judge the result."""
    before = _post(f"{SIMULATOR}/scenario", {"name": scenario})
    time.sleep(SETTLE_SECONDS)
    after = _get(f"{SIMULATOR}/status")
    after["campaigns_changed"] = before.get("campaigns_changed", 0)
    return evaluate_scenario(before, after, scenario)


def main() -> int:
    """Run the Level 0 gate against the live stack and print the scoreboard."""
    checks = _health_checks()
    checks.append(_targets_check())

    for scenario in ("steady", "error_burst", "traffic_surge", "bad_config_deploy"):
        checks.append(_run_scenario(scenario))

    # budget_runaway is judged on the config change it makes, then reverted by
    # restoring steady traffic against a freshly seeded platform.
    checks.append(_run_scenario("budget_runaway"))
    _post(f"{SIMULATOR}/scenario", {"name": "steady"})

    passed, total = summarize(checks)
    width = max(len(check.name) for check in checks)
    print()
    for check in checks:
        mark = "PASS" if check.passed else "FAIL"
        print(f"  [{mark}] {check.name.ljust(width)}  {check.detail}")
    print(f"\n  LEVEL 0 GATE: {passed}/{total} ({passed / total:.0%})\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
```

Create `platform/README.md` explaining that `platform_gates.py` runs against the live stack (`docker compose up -d` first), is not part of the hermetic suite, and that its *decision logic* is unit-tested in `tests/platform/test_level0_gate.py`.

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `python -m uv run pytest tests/platform -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Run the gate against the live stack**

```bash
docker compose up -d --build
docker compose restart prometheus grafana
python -m uv run python platform/platform_gates.py
```

Expected: `LEVEL 0 GATE: 10/10 (100%)`. Record the real number — if it is not 10/10, fix what it found; the gate is the deliverable, not the pass mark.

- [ ] **Step 6: Commit**

```bash
git add platform tests/platform pyproject.toml
git commit -m "feat: Level 0 quality gate — health, scrape targets, and all five failure modes"
```

---

### Task 7: Documentation and definition of done

**Files:**
- Create: `docs/adr/0005-real-failures-not-mocked.md`, `docs/devlog/day-05.md`, `docs/runbooks/level-0-substrate.md`
- Modify: `docs/site/index.html`

- [ ] **Step 1: Write ADR-0005**

Create `docs/adr/0005-real-failures-not-mocked.md` following the ADR template. Decision: **failure modes are injected by changing real state through public APIs, never by mocking a symptom or by a debug flag the substrate reads.** Context: Level 3's ops agents will be evaluated on whether they find the *right* root cause; if the failure is a mock, the only findable cause is the mock, and the eval measures nothing. Alternatives: a `CHAOS=true` env var read by each service (rejected — it puts test code in the serving path and the "cause" is a branch, not a config); a proxy that drops packets (rejected — it produces failures with no configuration cause to find, which is the *least* interesting incident class for a config-driven ads platform); mocked Prometheus data (rejected outright — mock-data theater is the thing this project exists to not be). Consequences: injecting a failure **really breaks the running platform** and the simulator must be able to restore it; the seed campaign set is therefore part of the product, not a fixture. **Trigger:** if a needed failure class has no configuration cause — a genuine network partition, say — that is when a fault-injection proxy earns its place alongside this, not instead of it.

- [ ] **Step 2: Write the Level 0 runbook**

Create `docs/runbooks/level-0-substrate.md` — the first runbook, and deliberate context-layer fodder for Level 1. Cover: what each service does and its port; how to start and stop the stack; how to read the two dashboards; the five failure modes with the signal each produces and how to revert it; how to run the Level 0 gate; and a symptom table (fill rate at zero → check targeting and the injected scenario; error ratio spiking → check `error_burst` and the 422 bodies; p95 climbing with rate → load, not fault; duplicate count climbing → a client retrying, working as designed).

- [ ] **Step 3: Update the running doc**

In `docs/site/index.html`:
1. Top bar `DAY <b>04</b>` → `DAY <b>05</b>`; footer `DAY 04 / 30` → `DAY 05 / 30`.
2. Pod head `4 shipped · 26 queued` → `5 shipped · 25 queued`.
3. Pod strip: add `nohead` to the Day 4 segment; change the Day 5 segment to `class="seg shipped"`.
4. Tracker row `05` → `<td class="st done">● SHIPPED</td>`.
5. **Eval scoreboard**: change the L0 row's Actual cell from `—` to the gate's real result (e.g. `<td class="mono">10/10</td>`). This is the first published number on the board — it must be the number the gate actually printed.
6. Add a "Day 5 — the failure injection loop" Mermaid diagram to the Level 0 section:

```html
    <h3>Day 5 — the failure injection loop</h3>
    <p>The simulator does not fake a symptom. It changes real configuration through campaign-service's public API, so the incident that follows has an actual cause sitting in an actual table — which is the only way Level 3's RCA agent can be honestly evaluated.</p>
    <pre class="mermaid">
flowchart LR
    OP["POST /scenario&lt;br/&gt;{bad_config_deploy}"] --> SIM["traffic-simulator"]
    SIM -->|"PATCH /campaigns/{id}&lt;br/&gt;targeting → AQ"| CS["campaign-service"]
    SIM -->|"POST /ad-request&lt;br/&gt;at scenario rate"| ADS["ad-decision-service"]
    ADS -->|"reads changed config"| CS
    ADS --> NOFILL["targeting_mismatch&lt;br/&gt;fill rate → 0"]
    SIM -->|"impression / click"| EV["event-service"]
    NOFILL --> PROM["Prometheus"]
    EV --> PROM
    PROM --> GRAF["Grafana&lt;br/&gt;the incident, visible"]
    GRAF -.->|"Level 3"| RCA["ops agent&lt;br/&gt;finds the real cause"]
    </pre>
```

7. Add an ADR-0005 card to SEQ 04, above the ADR-0004 card, summarizing "real failures, not mocked" and its trigger.

- [ ] **Step 4: Write the devlog**

Create `docs/devlog/day-05.md` in the established format — `# Day 5 — traffic-simulator + Level 0 quality gate`, `**Level:** 0 · **Date:** 2026-07-21`, then `## Shipped`, `## Decisions`, `## For the video`, `## Tomorrow`. The video shot list should cover: `GET /scenarios` reading the five failure modes off the API; the ads-delivery dashboard under `steady`; injecting `bad_config_deploy` live and watching fill rate fall while the `targeting_mismatch` band takes over; showing the *actual* PATCH in campaign-service that caused it (the real-cause point, ADR-0005); `error_burst` lighting the error-ratio panel; reverting to `steady` and watching it recover; running the Level 0 gate on screen and reading the score; and the eval scoreboard's first published number. "Tomorrow" opens Level 1 — the context layer's document ingestion pipeline, with everything built in Level 0 as its corpus.

- [ ] **Step 5: Full verification, gates unpiped**

```bash
python -m uv run pytest
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate
docker compose ps
python -m uv run python platform/platform_gates.py
```

- [ ] **Step 6: Commit**

```bash
git add docs
git commit -m "docs: ADR-0005, Level 0 runbook, day-05 devlog, first published gate number"
```

---

## Execution notes (what the plan missed)

Recorded during execution, so the next plan does not repeat these:

1. **The seed guard was all-or-nothing.** `seed_if_empty` skipped the whole set if *any* campaign existed, so one leftover campaign from a Day 4 manual test starved the simulator to a 2% fill rate. Replaced with `seed_campaigns`, idempotent per campaign by name.
2. **Budgets overflow a 32-bit column.** `Integer` in SQLAlchemy is Postgres `INTEGER`, capping micros at 2,147,483,647 (~$2,147/day). The planned `RUNAWAY_MULTIPLIER = 1000` guaranteed a `NumericValueOutOfRange`. Fixed with explicit int32-safe constants, a `le=` bound in the schema, and a `DataError` handler. **Any future plan touching micros must respect this ceiling** until the column becomes `BIGINT`.
3. **A bare 500 was hiding behind it.** The overflow violated the project's own "never a bare 500" standard. Range errors are now typed 422s.
4. **Reverting a real failure needs a real rollback.** The plan gave `steady` no `config_mutation`, so after `bad_config_deploy` the platform stayed broken forever. `steady` now restores the seed configuration — and that turned out to be the more interesting design, since rollback is what a Level 3 agent should recommend.
5. **Request rate must be sized against the campaign budgets.** At the planned 5 rps, pacing correctly throttled nearly everything and a healthy baseline looked identical to an injected failure. 2 rps gives a ~40% baseline.
6. **`platform` is a stdlib module name.** The directory cannot be a Python package. It goes on `pythonpath` and the module imports as `level0_gate`.
7. **SQLite does not enforce int4**, so a hermetic test cannot reproduce a Postgres range error. Bound the value in the Pydantic schema (testable, and part of the contract) and test the error handler directly.
8. **A gate that reads its baseline from the mutating call measures nothing.** `POST /scenario` returns state *after* the switch, so comparing against it made the traffic-surge check compare 20 rps to 20 rps. Read the baseline with a separate `GET /status` first.
9. `ruff format` fixes E501 but not ANN401 — helpers returning parsed JSON need a concrete `dict[str, Any]` annotation, not `Any`.
