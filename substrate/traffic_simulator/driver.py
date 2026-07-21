"""One tick of the simulation: request an ad, then report what happened.

No sleeping, no clock, no network of its own — the loop that calls this owns the
timing and the control plane owns the clients. That is what makes the interesting
part (what gets reported, and when) testable without infrastructure.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from substrate.traffic_simulator.clients import SubstrateClients
from substrate.traffic_simulator.population import Population
from substrate.traffic_simulator.scenarios import Scenario, corrupt


@dataclass(frozen=True)
class TickResult:
    """What one ad request turned into."""

    status_code: int
    filled: bool
    no_fill_reason: str | None
    events_reported: int


def _event(
    event_type: str, decision: dict[str, Any], ad: dict[str, Any], price_micros: int
) -> dict[str, Any]:
    """A delivery report for one decision, with its own idempotency key."""
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "request_id": decision["request_id"],
        "campaign_id": ad["campaign_id"],
        "creative_id": ad["creative_id"],
        "member_id": decision.get("member_id", "member-unknown"),
        "slot_id": decision["slot_id"],
        "price_micros": price_micros,
        "occurred_at": datetime.now(UTC).isoformat(),
    }


def tick(
    clients: SubstrateClients,
    population: Population,
    scenario: Scenario,
    random_source: random.Random,
    click_probability: float,
) -> TickResult:
    """Run one ad request end to end and report the delivery it produced."""
    request = population.ad_request()
    if random_source.random() < scenario.malformed_fraction:
        request = corrupt(request)

    member = request["member"]
    member_id = member["member_id"] if isinstance(member, dict) else "member-unknown"

    status_code, decision = clients.ad_request(request)
    if status_code != 200:
        return TickResult(status_code, filled=False, no_fill_reason=None, events_reported=0)

    ad = decision.get("ad")
    if not decision.get("filled") or not isinstance(ad, dict):
        return TickResult(
            status_code,
            filled=False,
            no_fill_reason=decision.get("no_fill_reason"),
            events_reported=0,
        )

    decision = {**decision, "member_id": member_id}
    reported = 0
    clients.report_event(_event("impression", decision, ad, int(ad["price_micros"])))
    reported += 1
    if random_source.random() < click_probability:
        clients.report_event(_event("click", decision, ad, 0))
        reported += 1

    return TickResult(status_code, filled=True, no_fill_reason=None, events_reported=reported)
