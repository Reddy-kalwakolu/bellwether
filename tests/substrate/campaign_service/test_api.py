"""API contract tests: one happy path and one failure path per endpoint."""

from typing import Any

from fastapi.testclient import TestClient


def _create(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/campaigns", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_health_reports_service_name(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "campaign-service"}


def test_create_campaign_persists_targeting(
    client: TestClient, campaign_payload: dict[str, Any]
) -> None:
    body = _create(client, campaign_payload)

    assert body["advertiser"] == "Acme Snacks"
    assert body["targeting"]["countries"] == ["US", "CA"]
    assert body["brand_safety_exclusions"] == ["news", "true-crime"]
    assert body["creatives"] == []


def test_create_campaign_rejects_daily_budget_above_total(
    client: TestClient, campaign_payload: dict[str, Any]
) -> None:
    campaign_payload["daily_budget_micros"] = campaign_payload["budget_micros"] + 1

    response = client.post("/campaigns", json=campaign_payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == 422


def test_create_campaign_rejects_inverted_flight_window(
    client: TestClient, campaign_payload: dict[str, Any]
) -> None:
    campaign_payload["ends_at"] = campaign_payload["starts_at"]

    assert client.post("/campaigns", json=campaign_payload).status_code == 422


def test_list_campaigns_filters_by_status(
    client: TestClient, campaign_payload: dict[str, Any]
) -> None:
    _create(client, campaign_payload)
    paused = dict(campaign_payload, name="Paused flight", status="paused")
    _create(client, paused)

    active = client.get("/campaigns", params={"status": "active"}).json()

    assert [c["status"] for c in active] == ["active"]


def test_list_campaigns_rejects_unknown_status(client: TestClient) -> None:
    assert client.get("/campaigns", params={"status": "archived"}).status_code == 422


def test_get_campaign_returns_typed_404(client: TestClient) -> None:
    response = client.get("/campaigns/0f14d0ab-9605-4a62-a9e4-5ed26688389b")

    assert response.status_code == 404
    assert response.json()["error"]["message"].startswith("campaign ")


def test_patch_campaign_pauses_flight(client: TestClient, campaign_payload: dict[str, Any]) -> None:
    campaign_id = _create(client, campaign_payload)["id"]

    response = client.patch(f"/campaigns/{campaign_id}", json={"status": "paused"})

    assert response.status_code == 200
    assert response.json()["status"] == "paused"


def test_patch_missing_campaign_returns_404(client: TestClient) -> None:
    response = client.patch(
        "/campaigns/0f14d0ab-9605-4a62-a9e4-5ed26688389b", json={"status": "paused"}
    )

    assert response.status_code == 404


def test_delete_campaign_then_get_returns_404(
    client: TestClient, campaign_payload: dict[str, Any]
) -> None:
    campaign_id = _create(client, campaign_payload)["id"]

    assert client.delete(f"/campaigns/{campaign_id}").status_code == 204
    assert client.get(f"/campaigns/{campaign_id}").status_code == 404


def test_delete_missing_campaign_returns_404(client: TestClient) -> None:
    assert client.delete("/campaigns/0f14d0ab-9605-4a62-a9e4-5ed26688389b").status_code == 404


def test_add_and_list_creatives(client: TestClient, campaign_payload: dict[str, Any]) -> None:
    campaign_id = _create(client, campaign_payload)["id"]
    creative = {
        "name": "30s hero spot",
        "duration_seconds": 30,
        "asset_url": "https://cdn.example/hero.mp4",
    }

    created = client.post(f"/campaigns/{campaign_id}/creatives", json=creative)
    listed = client.get(f"/campaigns/{campaign_id}/creatives")

    assert created.status_code == 201
    assert listed.status_code == 200
    assert [c["name"] for c in listed.json()] == ["30s hero spot"]


def test_add_creative_to_missing_campaign_returns_404(client: TestClient) -> None:
    creative = {"name": "spot", "duration_seconds": 15, "asset_url": "https://cdn/x.mp4"}

    response = client.post(
        "/campaigns/0f14d0ab-9605-4a62-a9e4-5ed26688389b/creatives", json=creative
    )

    assert response.status_code == 404


def test_add_creative_rejects_overlong_duration(
    client: TestClient, campaign_payload: dict[str, Any]
) -> None:
    campaign_id = _create(client, campaign_payload)["id"]
    creative = {"name": "epic", "duration_seconds": 600, "asset_url": "https://cdn/x.mp4"}

    assert client.post(f"/campaigns/{campaign_id}/creatives", json=creative).status_code == 422


def test_list_creatives_for_missing_campaign_returns_404(client: TestClient) -> None:
    assert (
        client.get("/campaigns/0f14d0ab-9605-4a62-a9e4-5ed26688389b/creatives").status_code == 404
    )


def test_metrics_endpoint_reports_request_counter(client: TestClient) -> None:
    client.get("/health")

    body = client.get("/metrics").text

    assert "http_requests_total" in body
    assert 'service="campaign-service"' in body
