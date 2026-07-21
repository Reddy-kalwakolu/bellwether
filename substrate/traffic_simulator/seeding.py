"""A realistic campaign set, and the config mutations that break it.

Both halves talk to campaign-service through its public API. Nothing here reaches
into a database, and nothing fakes a symptom: a bad config deploy really does
deploy a bad config (ADR-0005).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from substrate.traffic_simulator.clients import SubstrateClients

# Somewhere with no traffic. Retargeting every campaign here is a plausible
# fat-fingered deploy, and it makes fill rate fall off a cliff within seconds.
DEAD_COUNTRY = "AQ"

# campaign-service stores micros in a 32-bit integer column, so every budget here
# has to stay under 2_147_483_647. That ceiling is ~$2,147 of daily budget — fine
# for a substrate, wrong for a real platform, and it is why budget_micros is
# daily * 30 only while that product still fits. See day-05 devlog.
INT32_MAX = 2_147_483_647
RUNAWAY_DAILY_BUDGET_MICROS = 2_000_000_000
RUNAWAY_TOTAL_BUDGET_MICROS = 2_100_000_000


def _flight() -> tuple[str, str]:
    """A flight window that is open now and stays open for a month."""
    start = datetime.now(UTC) - timedelta(days=1)
    return start.isoformat(), (start + timedelta(days=30)).isoformat()


def _campaign(
    name: str,
    advertiser: str,
    daily_budget_micros: int,
    frequency_cap_per_day: int,
    countries: list[str],
    device_types: list[str],
    content_ratings: list[str],
    exclusions: list[str],
) -> dict[str, Any]:
    """One campaign body for campaign-service."""
    starts_at, ends_at = _flight()
    return {
        "name": name,
        "advertiser": advertiser,
        "status": "active",
        "budget_micros": min(daily_budget_micros * 30, INT32_MAX),
        "daily_budget_micros": daily_budget_micros,
        "frequency_cap_per_day": frequency_cap_per_day,
        "targeting": {
            "countries": countries,
            "device_types": device_types,
            "content_ratings": content_ratings,
        },
        "brand_safety_exclusions": exclusions,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


SEED_CAMPAIGNS: list[dict[str, Any]] = [
    {
        "campaign": _campaign(
            "Wide-reach snack launch",
            "Acme Snacks",
            daily_budget_micros=60_000_000,
            frequency_cap_per_day=3,
            countries=["US", "CA"],
            device_types=["tv", "mobile", "tablet", "desktop"],
            content_ratings=["TV-G", "TV-14"],
            exclusions=["news", "true-crime"],
        ),
        "creatives": [
            {
                "name": "30s hero spot",
                "duration_seconds": 30,
                "asset_url": "https://cdn.example/snack-30.mp4",
            },
            {
                "name": "15s cutdown",
                "duration_seconds": 15,
                "asset_url": "https://cdn.example/snack-15.mp4",
            },
        ],
    },
    {
        "campaign": _campaign(
            "Premium sedan, connected TV only",
            "Northwind Motors",
            daily_budget_micros=65_000_000,
            frequency_cap_per_day=2,
            countries=["US", "CA", "GB", "DE"],
            device_types=["tv"],
            content_ratings=["TV-14", "TV-MA"],
            exclusions=["true-crime"],
        ),
        "creatives": [
            {
                "name": "60s cinematic",
                "duration_seconds": 60,
                "asset_url": "https://cdn.example/sedan-60.mp4",
            },
            {
                "name": "30s cutdown",
                "duration_seconds": 30,
                "asset_url": "https://cdn.example/sedan-30.mp4",
            },
        ],
    },
    {
        "campaign": _campaign(
            "Sports drink, mobile takeover",
            "Vertex Hydration",
            daily_budget_micros=50_000_000,
            frequency_cap_per_day=5,
            countries=["US", "BR", "GB"],
            device_types=["mobile", "tablet"],
            content_ratings=["TV-G", "TV-14", "TV-MA"],
            exclusions=[],
        ),
        "creatives": [
            {
                "name": "15s bumper",
                "duration_seconds": 15,
                "asset_url": "https://cdn.example/drink-15.mp4",
            },
        ],
    },
]


def seed_campaigns(clients: SubstrateClients) -> int:
    """Create any seed campaign that is missing, by name. Returns how many it created.

    Idempotent per campaign rather than all-or-nothing. An earlier all-or-nothing
    guard meant one leftover campaign from a manual test suppressed the whole seed
    set, and the simulator then drove traffic at a single narrowly-targeted flight
    with a 2% fill rate. Identity is the name; re-running this is always safe.
    """
    existing = {campaign.get("name") for campaign in clients.list_campaigns()}
    created = 0
    for entry in SEED_CAMPAIGNS:
        if entry["campaign"]["name"] in existing:
            continue
        campaign = clients.create_campaign(entry["campaign"])
        for creative in entry["creatives"]:
            clients.add_creative(str(campaign["id"]), creative)
        created += 1
    return created


def apply_mutation(clients: SubstrateClients, mutation: str) -> int:
    """Inject a configuration failure. Returns how many campaigns were changed."""
    campaigns = clients.list_campaigns()
    if not campaigns:
        return 0

    if mutation == "retarget_all_campaigns":
        for campaign in campaigns:
            clients.patch_campaign(
                str(campaign["id"]),
                {
                    "targeting": {
                        "countries": [DEAD_COUNTRY],
                        "device_types": [],
                        "content_ratings": [],
                    }
                },
            )
        return len(campaigns)

    if mutation == "restore_seed_config":
        # The rollback. Injected failures change real configuration, so recovering
        # has to change it back — which is the same operation an ops agent would
        # recommend in Level 3, not a special "undo" the platform knows about.
        by_name = {entry["campaign"]["name"]: entry["campaign"] for entry in SEED_CAMPAIGNS}
        restored = 0
        for campaign in campaigns:
            seed = by_name.get(campaign.get("name"))
            if seed is None:
                continue
            clients.patch_campaign(
                str(campaign["id"]),
                {
                    "targeting": seed["targeting"],
                    "daily_budget_micros": seed["daily_budget_micros"],
                    "budget_micros": seed["budget_micros"],
                    "frequency_cap_per_day": seed["frequency_cap_per_day"],
                    "status": "active",
                },
            )
            restored += 1
        return restored

    if mutation == "inflate_one_daily_budget":
        target = campaigns[0]
        clients.patch_campaign(
            str(target["id"]),
            {
                "daily_budget_micros": RUNAWAY_DAILY_BUDGET_MICROS,
                "budget_micros": RUNAWAY_TOTAL_BUDGET_MICROS,
                "frequency_cap_per_day": 50,
            },
        )
        return 1

    return 0
