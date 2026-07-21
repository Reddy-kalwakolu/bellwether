"""One tick of the simulation loop, with no network and no clock."""

from __future__ import annotations

import random
from typing import Any

from substrate.traffic_simulator.driver import TickResult, tick
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


def run(
    clients: RecordingClients, scenario_name: str = "steady", click_probability: float = 0.0
) -> TickResult:
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
    population = Population(seed=1)
    source = random.Random(1)
    for _ in range(20):
        tick(clients, population, get("error_burst"), random_source=source, click_probability=0.0)

    corrupted = [
        request for request in clients.requests if request["slot"]["duration_seconds"] == 0
    ]
    assert corrupted, "error_burst never corrupted a request"


def test_steady_never_corrupts_a_request() -> None:
    clients = RecordingClients()
    population = Population(seed=2)
    source = random.Random(3)
    for _ in range(20):
        tick(clients, population, get("steady"), random_source=source, click_probability=0.0)

    assert all(request["slot"]["duration_seconds"] > 0 for request in clients.requests)
