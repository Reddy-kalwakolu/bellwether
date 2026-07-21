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
RUNAWAY_MULTIPLIER = 1_000


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
        "budget_micros": daily_budget_micros * 30,
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
            daily_budget_micros=50_000_000,
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
            daily_budget_micros=120_000_000,
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
            daily_budget_micros=20_000_000,
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


def seed_if_empty(clients: SubstrateClients) -> int:
    """Create the seed campaign set, unless the platform already has campaigns."""
    if clients.list_campaigns():
        return 0
    created = 0
    for entry in SEED_CAMPAIGNS:
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

    if mutation == "inflate_one_daily_budget":
        target = campaigns[0]
        current = int(target.get("daily_budget_micros") or 1_000_000)
        clients.patch_campaign(
            str(target["id"]),
            {
                "daily_budget_micros": current * RUNAWAY_MULTIPLIER,
                "budget_micros": current * RUNAWAY_MULTIPLIER * 30,
                "frequency_cap_per_day": 50,
            },
        )
        return 1

    return 0
