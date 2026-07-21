"""The client that reads active campaigns from campaign-service over HTTP."""

from __future__ import annotations

from collections.abc import Callable

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


def client_with(handler: Callable[[httpx.Request], httpx.Response]) -> CampaignClient:
    """A CampaignClient whose transport is a local handler, never a socket."""
    return CampaignClient(
        "http://campaign-service:8000",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )


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
