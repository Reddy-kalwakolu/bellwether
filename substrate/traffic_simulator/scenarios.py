"""The failure modes the simulator can inject.

Each scenario is data, not a branch buried in the driver. Two of them change real
campaign configuration through campaign-service's public API rather than faking a
symptom — see ADR-0005. That is what makes the resulting incident diagnosable:
there is an actual cause sitting in an actual table.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    """One injectable failure mode, and the three knobs it can turn."""

    name: str
    summary: str
    rate_multiplier: float = 1.0
    malformed_fraction: float = 0.0
    config_mutation: str | None = None


SCENARIOS: dict[str, Scenario] = {
    "steady": Scenario(
        name="steady",
        summary="Baseline traffic. Nothing is wrong; this is what healthy looks like.",
    ),
    "error_burst": Scenario(
        name="error_burst",
        summary="Three in ten ad requests are malformed. The error-ratio panel lifts "
        "and the 422s are attributable to a caller, not to the service.",
        malformed_fraction=0.30,
    ),
    "traffic_surge": Scenario(
        name="traffic_surge",
        summary="Ten times the request rate. Throughput and p95 latency climb together, "
        "which is what load looks like as distinct from a fault.",
        rate_multiplier=10.0,
    ),
    "bad_config_deploy": Scenario(
        name="bad_config_deploy",
        summary="Every campaign is retargeted to a country the traffic never comes from. "
        "Fill rate collapses and targeting_mismatch takes over the loss breakdown.",
        config_mutation="retarget_all_campaigns",
    ),
    "budget_runaway": Scenario(
        name="budget_runaway",
        summary="One campaign's daily budget is inflated a thousandfold. Pacing stops "
        "throttling it and it takes every slot it is eligible for.",
        config_mutation="inflate_one_daily_budget",
    ),
}


def get(name: str) -> Scenario:
    """Look up a scenario by name, raising KeyError for anything unknown."""
    return SCENARIOS[name]


def corrupt(request: dict[str, object]) -> dict[str, object]:
    """Return a copy of `request` that the ad-decision API is required to reject.

    `duration_seconds` is `gt=0` on the Slot model, so zero is a guaranteed 422 —
    a caller error, which is exactly the signal this scenario is meant to produce.
    """
    corrupted = copy.deepcopy(request)
    slot = corrupted["slot"]
    if isinstance(slot, dict):
        slot["duration_seconds"] = 0
    return corrupted
