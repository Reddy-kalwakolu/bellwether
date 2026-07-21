"""Seeding a realistic campaign set, and mutating it to inject a failure."""

from __future__ import annotations

from typing import Any

from substrate.traffic_simulator.seeding import SEED_CAMPAIGNS, apply_mutation, seed_if_empty


class FakeClients:
    """Records what the simulator would have done to campaign-service."""

    def __init__(self, campaigns: list[dict[str, Any]] | None = None) -> None:
        self.campaigns = campaigns if campaigns is not None else []
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

    def ad_request(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {}

    def report_event(self, payload: dict[str, Any]) -> int:
        return 201


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
