"""Endpoint contracts: idempotent ingest, delivery rollups, metrics, typed errors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.substrate.event_service.conftest import CAMPAIGN_ID

CAMPAIGN_B = "33333333-3333-3333-3333-333333333333"


def other(payload: dict[str, Any], **overrides: object) -> dict[str, Any]:
    """A distinct event derived from `payload`."""
    body = dict(payload)
    body["event_id"] = str(uuid4())
    body.update(overrides)
    return body


def test_health_reports_the_service_name(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "event-service"}


def test_an_impression_is_recorded(client: TestClient, impression_payload: dict[str, Any]) -> None:
    response = client.post("/events", json=impression_payload)
    assert response.status_code == 201
    assert response.json() == {
        "event_id": impression_payload["event_id"],
        "status": "recorded",
    }


def test_replaying_an_event_id_is_a_duplicate_not_a_second_impression(
    client: TestClient, impression_payload: dict[str, Any]
) -> None:
    assert client.post("/events", json=impression_payload).status_code == 201

    replay = client.post("/events", json=impression_payload)
    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate"

    delivery = client.get(f"/campaigns/{CAMPAIGN_ID}/delivery").json()
    assert delivery["impressions"] == 1
    assert delivery["spend_micros"] == 2_000


def test_delivery_reports_impressions_clicks_ctr_and_spend(
    client: TestClient, impression_payload: dict[str, Any]
) -> None:
    for _ in range(4):
        client.post("/events", json=other(impression_payload))
    client.post("/events", json=other(impression_payload, event_type="click", price_micros=0))

    delivery = client.get(f"/campaigns/{CAMPAIGN_ID}/delivery").json()
    assert delivery["impressions"] == 4
    assert delivery["clicks"] == 1
    assert delivery["click_through_rate"] == 0.25
    assert delivery["spend_micros"] == 8_000


def test_delivery_for_an_unknown_campaign_is_zeroes_not_a_404(client: TestClient) -> None:
    response = client.get(f"/campaigns/{uuid4()}/delivery")
    assert response.status_code == 200
    assert response.json()["impressions"] == 0


def test_the_rollup_lists_every_campaign_that_served(
    client: TestClient, impression_payload: dict[str, Any]
) -> None:
    client.post("/events", json=impression_payload)
    client.post("/events", json=other(impression_payload, campaign_id=CAMPAIGN_B))

    rollup = client.get("/delivery").json()
    assert len(rollup) == 2
    assert {row["campaign_id"] for row in rollup} == {CAMPAIGN_ID, CAMPAIGN_B}


def test_a_day_filter_narrows_the_rollup(
    client: TestClient, impression_payload: dict[str, Any]
) -> None:
    yesterday = datetime.now(UTC) - timedelta(days=1)
    client.post("/events", json=impression_payload)
    client.post("/events", json=other(impression_payload, occurred_at=yesterday.isoformat()))

    today = datetime.now(UTC).date().isoformat()
    rollup = client.get("/delivery", params={"day": today}).json()
    assert rollup[0]["impressions"] == 1


def test_recent_events_are_listed_newest_first(
    client: TestClient, impression_payload: dict[str, Any]
) -> None:
    older = datetime.now(UTC) - timedelta(hours=1)
    client.post("/events", json=other(impression_payload, occurred_at=older.isoformat()))
    client.post("/events", json=impression_payload)

    events = client.get("/events", params={"limit": 10}).json()
    assert [event["event_id"] for event in events][0] == impression_payload["event_id"]
    assert len(events) == 2


def test_listing_events_can_be_scoped_to_one_campaign(
    client: TestClient, impression_payload: dict[str, Any]
) -> None:
    client.post("/events", json=impression_payload)
    client.post("/events", json=other(impression_payload, campaign_id=CAMPAIGN_B))

    events = client.get("/events", params={"campaign_id": CAMPAIGN_ID}).json()
    assert len(events) == 1
    assert events[0]["campaign_id"] == CAMPAIGN_ID


def test_an_invalid_event_returns_the_typed_error_envelope(
    client: TestClient, impression_payload: dict[str, Any]
) -> None:
    impression_payload["event_type"] = "purchase"
    response = client.post("/events", json=impression_payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == 422


def test_metrics_expose_the_event_counters(
    client: TestClient, impression_payload: dict[str, Any]
) -> None:
    client.post("/events", json=impression_payload)
    client.post("/events", json=impression_payload)  # duplicate

    body = client.get("/metrics").text
    assert "ad_events_total" in body
    assert 'event_type="impression"' in body
    assert "ad_events_duplicate_total" in body
    assert "ad_spend_micros_total" in body
    assert "http_requests_total" in body
