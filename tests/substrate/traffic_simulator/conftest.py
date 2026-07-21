"""Fixtures backing traffic-simulator tests. No sockets, no background loop."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from substrate.traffic_simulator.clients import build_clients
from substrate.traffic_simulator.main import SimulatorState, app, get_state


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
    state = SimulatorState()
    app.dependency_overrides[build_clients] = lambda: stub_clients
    app.dependency_overrides[get_state] = lambda: state
    yield TestClient(app)
    app.dependency_overrides.clear()
