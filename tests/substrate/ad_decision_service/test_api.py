"""Endpoint contracts: a fill, each no-fill reason, and the failure paths."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from substrate.ad_decision_service.campaign_client import CampaignServiceError
from substrate.ad_decision_service.store import InMemoryDecisionStore

CAMPAIGN_ID = "11111111-1111-1111-1111-111111111111"
ClientFactory = Callable[..., TestClient]


def test_health_reports_the_service_name(make_client: ClientFactory) -> None:
    response = make_client([]).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ad-decision-service"}


def test_a_matching_request_fills_the_slot(
    make_client: ClientFactory,
    active_campaign: dict[str, Any],
    ad_request_body: dict[str, Any],
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
            "campaign_id": CAMPAIGN_ID,
            "campaign_name": "Stranger Things S5 Launch",
            "reason": "eligible",
        }
    ]


def test_serving_records_the_impression_against_the_frequency_cap(
    make_client: ClientFactory,
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
    assert store.daily_spend_micros(CAMPAIGN_ID, datetime.now(UTC).date()) == 4_000


def test_brand_safety_produces_an_explained_no_fill(
    make_client: ClientFactory,
    active_campaign: dict[str, Any],
    ad_request_body: dict[str, Any],
) -> None:
    ad_request_body["slot"]["content_categories"] = ["true-crime"]
    body = make_client([active_campaign]).post("/ad-request", json=ad_request_body).json()
    assert body["filled"] is False
    assert body["no_fill_reason"] == "brand_safety_excluded"
    assert body["ad"] is None


def test_no_candidates_at_all_is_a_clean_no_fill(
    make_client: ClientFactory, ad_request_body: dict[str, Any]
) -> None:
    body = make_client([]).post("/ad-request", json=ad_request_body).json()
    assert body["filled"] is False
    assert body["no_fill_reason"] == "no_candidates"
    assert body["candidates_considered"] == 0


def test_an_invalid_request_returns_the_typed_error_envelope(
    make_client: ClientFactory, ad_request_body: dict[str, Any]
) -> None:
    ad_request_body["slot"]["duration_seconds"] = 0
    response = make_client([]).post("/ad-request", json=ad_request_body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == 422


def test_an_unreachable_campaign_service_returns_503_not_500(
    make_client: ClientFactory, ad_request_body: dict[str, Any]
) -> None:
    client = make_client([], CampaignServiceError("connection refused"))
    response = client.post("/ad-request", json=ad_request_body)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == 503
    assert "campaign-service" in response.json()["error"]["message"]


def test_metrics_expose_decision_counters(
    make_client: ClientFactory,
    active_campaign: dict[str, Any],
    ad_request_body: dict[str, Any],
) -> None:
    client = make_client([active_campaign])
    client.post("/ad-request", json=ad_request_body)
    body = client.get("/metrics").text
    assert "ad_decisions_total" in body
    assert 'outcome="filled"' in body
    assert "ad_candidates_filtered_total" in body
    assert "http_requests_total" in body
