"""Fixtures backing ad-decision-service tests. No Redis, no live campaign-service."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from substrate.ad_decision_service.campaign_client import build_client
from substrate.ad_decision_service.decisioning import Candidate
from substrate.ad_decision_service.main import app, get_store
from substrate.ad_decision_service.store import InMemoryDecisionStore

CAMPAIGN_ID = "11111111-1111-1111-1111-111111111111"

ClientFactory = Callable[..., TestClient]


class StubCampaignClient:
    """Returns a fixed campaign set, or raises whatever the test asks it to."""

    def __init__(self, campaigns: list[dict[str, Any]], error: Exception | None = None) -> None:
        self.campaigns = campaigns
        self.error = error

    def fetch_active_campaigns(self) -> list[Candidate]:
        """Stand in for CampaignClient.fetch_active_campaigns, without a socket."""
        if self.error is not None:
            raise self.error
        return [Candidate.from_api(payload) for payload in self.campaigns]

    def close(self) -> None:
        """No pool to release."""


@pytest.fixture
def active_campaign() -> dict[str, Any]:
    """One in-flight campaign with a 30-second creative and a cap of two per day."""
    now = datetime.now(UTC)
    return {
        "id": CAMPAIGN_ID,
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
                "campaign_id": CAMPAIGN_ID,
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
def make_client(store: InMemoryDecisionStore) -> Iterator[ClientFactory]:
    """Build a TestClient whose campaign set — or upstream failure — the test chooses.

    The client is constructed without entering its context manager on purpose: the
    lifespan configures process-wide logging, and these tests deliberately need no
    infrastructure of any kind.
    """

    def factory(campaigns: list[dict[str, Any]], error: Exception | None = None) -> TestClient:
        stub = StubCampaignClient(campaigns, error)
        app.dependency_overrides[build_client] = lambda: stub
        app.dependency_overrides[get_store] = lambda: store
        return TestClient(app)

    yield factory
    app.dependency_overrides.clear()
