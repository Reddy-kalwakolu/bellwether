"""The provisioned Grafana dashboards, validated without running Grafana.

A dashboard that fails to load shows up as an empty Grafana at demo time. These
checks are cheap, hermetic, and catch the mistakes that actually happen: a stray
comma, a panel with no query, a datasource uid that does not match the provisioned
one, a panel graphing a metric no service emits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

DASHBOARD_DIR = Path("infra/grafana/provisioning/dashboards")
DATASOURCE_FILE = Path("infra/grafana/provisioning/datasources/prometheus.yml")
DASHBOARDS = sorted(DASHBOARD_DIR.glob("*.json"))

# Every metric the substrate actually exposes on /metrics, plus Prometheus' own `up`.
SUBSTRATE_METRICS = {
    "up",
    "http_requests_total",
    "http_request_duration_seconds_bucket",
    "ad_decisions_total",
    "ad_candidates_filtered_total",
    "ad_events_total",
    "ad_events_duplicate_total",
    "ad_spend_micros_total",
    "sim_scenario_info",
    "sim_ad_requests_total",
    "sim_events_reported_total",
}


def load(path: Path) -> dict[str, Any]:
    """Parse one dashboard file."""
    dashboard: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return dashboard


def panels(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Every panel in a dashboard, including those nested in rows."""
    found: list[dict[str, Any]] = []
    for panel in dashboard.get("panels", []):
        found.append(panel)
        found.extend(panel.get("panels", []))
    return found


def test_both_dashboards_are_present() -> None:
    assert {path.name for path in DASHBOARDS} == {
        "substrate-health.json",
        "ads-delivery.json",
    }


def test_the_provider_points_at_the_dashboard_directory() -> None:
    provider = yaml.safe_load((DASHBOARD_DIR / "dashboards.yml").read_text(encoding="utf-8"))
    assert provider["apiVersion"] == 1
    assert provider["providers"][0]["type"] == "file"
    assert provider["providers"][0]["options"]["path"] == "/etc/grafana/provisioning/dashboards"


def test_the_datasource_declares_the_uid_the_dashboards_reference() -> None:
    """Without a pinned uid Grafana invents one and every panel loads empty."""
    datasource = yaml.safe_load(DATASOURCE_FILE.read_text(encoding="utf-8"))
    assert datasource["datasources"][0]["uid"] == "prometheus"


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_a_dashboard_declares_a_title_a_uid_and_panels(path: Path) -> None:
    dashboard = load(path)
    assert dashboard["title"]
    assert dashboard["uid"] == path.stem
    assert panels(dashboard)


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_every_panel_queries_prometheus(path: Path) -> None:
    for panel in panels(load(path)):
        if panel["type"] == "row":
            continue
        assert panel["datasource"] == {"type": "prometheus", "uid": "prometheus"}, panel["title"]
        assert panel["targets"], panel["title"]
        for target in panel["targets"]:
            assert target["expr"].strip(), panel["title"]


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_panel_ids_are_unique_within_a_dashboard(path: Path) -> None:
    ids = [panel["id"] for panel in panels(load(path))]
    assert len(ids) == len(set(ids))


def test_dashboard_uids_are_unique() -> None:
    uids = [load(path)["uid"] for path in DASHBOARDS]
    assert len(uids) == len(set(uids))


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_the_dashboards_only_reference_metrics_the_substrate_emits(path: Path) -> None:
    for panel in panels(load(path)):
        for target in panel.get("targets", []):
            referenced = {name for name in SUBSTRATE_METRICS if name in target["expr"]}
            assert referenced, f"{path.name} / {panel['title']}: {target['expr']}"
