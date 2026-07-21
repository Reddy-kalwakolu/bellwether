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
    assert "http_requests_total" in body
    active = [
        line
        for line in body.splitlines()
        if line.startswith("sim_scenario_info{") and line.endswith(" 1.0")
    ]
    assert len(active) == 1
    assert 'scenario="error_burst"' in active[0]
