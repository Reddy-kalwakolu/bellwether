"""The decision path: pure rules, no I/O.

Every rejection returns a named reason rather than a bare False. Those names end up in
the response trace, in a Prometheus label, and in the JSON logs — which is what lets a
Level 3 ops agent answer "why did fill rate drop at 14:20?" without guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from substrate.ad_decision_service.schemas import AdRequest, FilterReason
from substrate.ad_decision_service.store import DecisionStore

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class CandidateCreative:
    """An ad asset that might fill the slot."""

    id: str
    name: str
    duration_seconds: int
    asset_url: str


def _parse_utc(value: str | datetime) -> datetime:
    """Coerce an API timestamp to an aware UTC datetime."""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Candidate:
    """A campaign as the decision path sees it, built from campaign-service JSON."""

    id: str
    name: str
    advertiser: str
    status: str
    daily_budget_micros: int
    frequency_cap_per_day: int
    targeting: dict[str, list[str]]
    brand_safety_exclusions: list[str]
    starts_at: datetime
    ends_at: datetime
    creatives: list[CandidateCreative] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Candidate:
        """Build a candidate from one campaign-service `CampaignRead` body."""
        return cls(
            id=str(payload["id"]),
            name=payload["name"],
            advertiser=payload["advertiser"],
            status=payload["status"],
            daily_budget_micros=int(payload["daily_budget_micros"]),
            frequency_cap_per_day=int(payload["frequency_cap_per_day"]),
            targeting={k: list(v) for k, v in (payload.get("targeting") or {}).items()},
            brand_safety_exclusions=list(payload.get("brand_safety_exclusions") or []),
            starts_at=_parse_utc(payload["starts_at"]),
            ends_at=_parse_utc(payload["ends_at"]),
            creatives=[
                CandidateCreative(
                    id=str(creative["id"]),
                    name=creative["name"],
                    duration_seconds=int(creative["duration_seconds"]),
                    asset_url=creative["asset_url"],
                )
                for creative in payload.get("creatives") or []
            ],
        )


def _targeting_matches(candidate: Candidate, request: AdRequest) -> bool:
    """An empty targeting list is unrestricted; a populated one must contain the value."""
    checks = (
        (candidate.targeting.get("countries", []), request.member.country),
        (candidate.targeting.get("device_types", []), request.member.device_type),
        (candidate.targeting.get("content_ratings", []), request.slot.content_rating),
    )
    return all(not allowed or value in allowed for allowed, value in checks)


def _brand_safety_allows(candidate: Candidate, request: AdRequest) -> bool:
    """Reject when the surrounding content carries a category the advertiser excluded."""
    excluded = {category.lower() for category in candidate.brand_safety_exclusions}
    return not excluded.intersection(
        category.lower() for category in request.slot.content_categories
    )


def pacing_allowance_micros(daily_budget_micros: int, now: datetime) -> int:
    """How much of today's budget a campaign is allowed to have spent by `now`.

    Even pacing: budget is released linearly across the day, so a campaign cannot
    exhaust its day in the first hour and then go dark through prime time.
    """
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (now.astimezone(UTC) - midnight).total_seconds()
    return int(daily_budget_micros * elapsed / SECONDS_PER_DAY)


def _fitting_creative(candidate: Candidate, request: AdRequest) -> CandidateCreative | None:
    """The longest creative that still fits the slot, or None."""
    fitting = [
        creative
        for creative in candidate.creatives
        if creative.duration_seconds <= request.slot.duration_seconds
    ]
    return max(fitting, key=lambda creative: creative.duration_seconds) if fitting else None


def evaluate(
    candidate: Candidate,
    request: AdRequest,
    store: DecisionStore,
    now: datetime,
    price_micros: int,
    pacing_enabled: bool,
) -> FilterReason:
    """Run one candidate through the chain, returning the first rule it fails."""
    if candidate.status != "active":
        return "not_active"
    if not candidate.starts_at <= now <= candidate.ends_at:
        return "outside_flight_window"
    if not _targeting_matches(candidate, request):
        return "targeting_mismatch"
    if not _brand_safety_allows(candidate, request):
        return "brand_safety_excluded"

    day = now.astimezone(UTC).date()
    seen = store.impression_count(request.member.member_id, candidate.id, day)
    if seen >= candidate.frequency_cap_per_day:
        return "frequency_capped"

    if pacing_enabled:
        spent = store.daily_spend_micros(candidate.id, day)
        allowance = pacing_allowance_micros(candidate.daily_budget_micros, now)
        # Floor the allowance at one impression, so the first request of the day fills.
        if spent + price_micros > max(allowance, price_micros):
            return "pacing_throttled"

    if _fitting_creative(candidate, request) is None:
        return "no_creative"
    return "eligible"


@dataclass(frozen=True)
class Outcome:
    """The winner (if any), plus the reason every candidate earned."""

    winner: Candidate | None
    creative: CandidateCreative | None
    trace: list[tuple[Candidate, FilterReason]]


def select(
    candidates: list[Candidate],
    request: AdRequest,
    store: DecisionStore,
    now: datetime,
    price_micros: int,
    pacing_enabled: bool,
) -> Outcome:
    """Filter every candidate, then award the slot to the least-delivered campaign."""
    trace: list[tuple[Candidate, FilterReason]] = []
    eligible: list[Candidate] = []
    for candidate in candidates:
        reason = evaluate(candidate, request, store, now, price_micros, pacing_enabled)
        trace.append((candidate, reason))
        if reason == "eligible":
            eligible.append(candidate)

    if not eligible:
        return Outcome(winner=None, creative=None, trace=trace)

    day = now.astimezone(UTC).date()

    def budget_remaining(candidate: Candidate) -> tuple[int, str]:
        """Rank key: most daily budget left wins, campaign id breaks ties."""
        remaining = candidate.daily_budget_micros - store.daily_spend_micros(candidate.id, day)
        return remaining, candidate.id

    winner = max(eligible, key=budget_remaining)
    return Outcome(winner=winner, creative=_fitting_creative(winner, request), trace=trace)
