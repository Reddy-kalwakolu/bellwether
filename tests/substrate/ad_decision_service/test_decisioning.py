"""Each rule in the filter chain, exercised on its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from substrate.ad_decision_service.decisioning import (
    Candidate,
    evaluate,
    pacing_allowance_micros,
    select,
)
from substrate.ad_decision_service.schemas import AdRequest, MemberContext, Slot
from substrate.ad_decision_service.store import DecisionStore, InMemoryDecisionStore

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)  # exactly half the day elapsed
PRICE = 2_000
CAMPAIGN_ID = "11111111-1111-1111-1111-111111111111"


def campaign_payload(**overrides: Any) -> dict[str, Any]:
    """A campaign-service `CampaignRead` body, as the HTTP client returns it."""
    payload: dict[str, Any] = {
        "id": CAMPAIGN_ID,
        "name": "Stranger Things S5 Launch",
        "advertiser": "Acme Snacks",
        "status": "active",
        "budget_micros": 500_000_000,
        "daily_budget_micros": 50_000_000,
        "frequency_cap_per_day": 3,
        "targeting": {
            "countries": ["US", "CA"],
            "device_types": ["tv"],
            "content_ratings": ["TV-14"],
        },
        "brand_safety_exclusions": ["news", "true-crime"],
        "starts_at": (NOW - timedelta(days=1)).isoformat(),
        "ends_at": (NOW + timedelta(days=29)).isoformat(),
        "created_at": (NOW - timedelta(days=1)).isoformat(),
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
    payload.update(overrides)
    return payload


def ad_request(**slot_overrides: Any) -> AdRequest:
    """An ad request that clears every rule against the default campaign."""
    slot: dict[str, Any] = {
        "slot_id": "slot-1",
        "duration_seconds": 30,
        "content_rating": "TV-14",
        "content_categories": ["drama"],
    }
    slot.update(slot_overrides)
    return AdRequest(
        member=MemberContext(member_id="member-1", country="US", device_type="tv"),
        slot=Slot(**slot),
    )


def check(
    candidate_payload: dict[str, Any], request: AdRequest, store: DecisionStore | None = None
) -> str:
    """Run one candidate through the chain and return the reason it earned."""
    return evaluate(
        Candidate.from_api(candidate_payload),
        request,
        store or InMemoryDecisionStore(),
        now=NOW,
        price_micros=PRICE,
        pacing_enabled=True,
    )


def test_a_matching_campaign_is_eligible() -> None:
    assert check(campaign_payload(), ad_request()) == "eligible"


def test_paused_campaigns_are_not_active() -> None:
    assert check(campaign_payload(status="paused"), ad_request()) == "not_active"


def test_a_campaign_whose_flight_has_ended_is_out_of_window() -> None:
    ended = campaign_payload(
        starts_at=(NOW - timedelta(days=10)).isoformat(),
        ends_at=(NOW - timedelta(days=1)).isoformat(),
    )
    assert check(ended, ad_request()) == "outside_flight_window"


def test_targeting_rejects_the_wrong_country_device_or_rating() -> None:
    request = ad_request()
    request.member.country = "GB"
    assert check(campaign_payload(), request) == "targeting_mismatch"

    request = ad_request()
    request.member.device_type = "mobile"
    assert check(campaign_payload(), request) == "targeting_mismatch"

    assert check(campaign_payload(), ad_request(content_rating="TV-MA")) == "targeting_mismatch"


def test_an_empty_targeting_list_means_unrestricted() -> None:
    unrestricted = campaign_payload(
        targeting={"countries": [], "device_types": [], "content_ratings": []}
    )
    request = ad_request(content_rating="TV-MA")
    request.member.country = "GB"
    assert check(unrestricted, request) == "eligible"


def test_brand_safety_excludes_a_campaign_from_flagged_content() -> None:
    request = ad_request(content_categories=["True-Crime"])
    assert check(campaign_payload(), request) == "brand_safety_excluded"


def test_a_member_at_the_frequency_cap_is_capped() -> None:
    store = InMemoryDecisionStore()
    for _ in range(3):
        store.record_impression("member-1", CAMPAIGN_ID, NOW.date(), PRICE)
    assert check(campaign_payload(), ad_request(), store) == "frequency_capped"


def test_a_campaign_spending_ahead_of_pace_is_throttled() -> None:
    store = InMemoryDecisionStore()
    # Half the day has elapsed, so the allowance is half of 1_000_000 micros.
    ahead = campaign_payload(daily_budget_micros=1_000_000)
    for _ in range(300):  # 600_000 micros spent against a 500_000 allowance
        store.record_impression("other-member", CAMPAIGN_ID, NOW.date(), PRICE)
    assert check(ahead, ad_request(), store) == "pacing_throttled"


def test_pacing_allows_the_first_impression_of_the_day() -> None:
    midnight = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
    assert pacing_allowance_micros(1_000_000, midnight) == 0
    reason = evaluate(
        Candidate.from_api(campaign_payload(daily_budget_micros=1_000_000)),
        ad_request(),
        InMemoryDecisionStore(),
        now=midnight,
        price_micros=PRICE,
        pacing_enabled=True,
    )
    assert reason == "eligible"


def test_a_slot_too_short_for_every_creative_has_no_creative() -> None:
    assert check(campaign_payload(), ad_request(duration_seconds=15)) == "no_creative"


def test_select_returns_the_campaign_with_the_most_daily_budget_remaining() -> None:
    poor = campaign_payload(
        id="33333333-3333-3333-3333-333333333333",
        name="Small flight",
        daily_budget_micros=10_000_000,
    )
    rich = campaign_payload(name="Big flight", daily_budget_micros=40_000_000)
    outcome = select(
        [Candidate.from_api(poor), Candidate.from_api(rich)],
        ad_request(),
        InMemoryDecisionStore(),
        now=NOW,
        price_micros=PRICE,
        pacing_enabled=True,
    )
    assert outcome.winner is not None
    assert outcome.winner.name == "Big flight"
    assert outcome.creative is not None
    assert outcome.creative.duration_seconds == 30
    assert {reason for _, reason in outcome.trace} == {"eligible"}


def test_select_reports_no_winner_and_a_full_trace_when_everything_is_filtered() -> None:
    outcome = select(
        [Candidate.from_api(campaign_payload(status="paused"))],
        ad_request(),
        InMemoryDecisionStore(),
        now=NOW,
        price_micros=PRICE,
        pacing_enabled=True,
    )
    assert outcome.winner is None
    assert [reason for _, reason in outcome.trace] == ["not_active"]
