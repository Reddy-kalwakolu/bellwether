"""The Level 0 quality gate.

The spec's Level 0 metric is "all services healthy under simulator load; failure
injection works — 100%". This turns that sentence into a script that either exits
zero or names the check that failed.

The decision logic is pure and unit-tested in `tests/platform/test_level0_gate.py`.
The live run only supplies numbers, which is what makes the gate trustworthy: it
cannot pass by accident while the substrate is down, and it cannot fail because a
test double drifted away from reality.

Run it against a running stack:

    docker compose up -d --build
    python -m uv run python platform/level0_gate.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

HEALTH_ENDPOINTS: dict[str, str] = {
    "campaign-service": "http://localhost:8001/health",
    "ad-decision-service": "http://localhost:8002/health",
    "event-service": "http://localhost:8003/health",
    "traffic-simulator": "http://localhost:8004/health",
    "prometheus": "http://localhost:9090/-/healthy",
}

SCRAPED_JOBS = {
    "campaign-service",
    "ad-decision-service",
    "event-service",
    "traffic-simulator",
}

SIMULATOR = "http://localhost:8004"
PROMETHEUS = "http://localhost:9090"

# Long enough for a 2 rps simulator to produce a decisive sample.
SETTLE_SECONDS = 30

# A healthy baseline is well above this; a bad config deploy is well below it.
COLLAPSED_FILL_RATE = 0.2
MIN_SAMPLE_REQUESTS = 10


@dataclass(frozen=True)
class GateCheck:
    """One gate criterion and whether the live stack met it."""

    name: str
    passed: bool
    detail: str


def evaluate_scenario(
    before: dict[str, float], after: dict[str, float], scenario: str
) -> GateCheck:
    """Decide whether an injected failure actually showed up in the numbers."""
    served = after.get("ticks", 0) - before.get("ticks", 0)
    filled = after.get("fills", 0) - before.get("fills", 0)

    if scenario == "steady":
        return GateCheck(
            "steady serves ads",
            served > 0 and filled > 0,
            f"{int(filled)} fills across {int(served)} requests",
        )

    if scenario == "error_burst":
        rejected = after.get("rejected", 0) - before.get("rejected", 0)
        return GateCheck(
            "error_burst produces rejections",
            rejected > 0,
            f"{int(rejected)} requests rejected",
        )

    if scenario == "traffic_surge":
        was, now = before.get("requests_per_second", 0), after.get("requests_per_second", 0)
        return GateCheck(
            "traffic_surge raises the rate",
            now > was,
            f"{was:g} -> {now:g} requests/sec",
        )

    if scenario == "bad_config_deploy":
        # Too small a sample is not evidence of collapse, so it is not a pass.
        enough = served >= MIN_SAMPLE_REQUESTS
        fill_rate = filled / served if served else 1.0
        return GateCheck(
            "bad_config_deploy collapses fill rate",
            enough and fill_rate < COLLAPSED_FILL_RATE,
            f"fill rate {fill_rate:.0%} across {int(served)} requests",
        )

    if scenario == "budget_runaway":
        changed = after.get("campaigns_changed", 0)
        return GateCheck(
            "budget_runaway changes configuration",
            changed > 0,
            f"{int(changed)} campaign(s) mutated",
        )

    return GateCheck(f"unknown scenario {scenario}", False, "no criterion defined")


def summarize(checks: list[GateCheck]) -> tuple[int, int]:
    """How many checks passed, out of how many."""
    return sum(1 for check in checks if check.passed), len(checks)


def _get(url: str) -> dict[str, Any]:
    """GET a URL and parse the JSON object it returns."""
    with urllib.request.urlopen(url, timeout=5) as response:
        body: dict[str, Any] = json.loads(response.read())
        return body


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST JSON and parse the object it returns."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body: dict[str, Any] = json.loads(response.read())
        return body


def _health_checks() -> list[GateCheck]:
    """Every substrate service answers its health endpoint."""
    checks: list[GateCheck] = []
    for service, url in HEALTH_ENDPOINTS.items():
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                checks.append(
                    GateCheck(
                        f"{service} healthy", response.status == 200, f"HTTP {response.status}"
                    )
                )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            checks.append(GateCheck(f"{service} healthy", False, str(exc)))
    return checks


def _targets_check() -> GateCheck:
    """Prometheus is scraping every substrate service."""
    try:
        body = _get(f"{PROMETHEUS}/api/v1/targets")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return GateCheck("prometheus scrapes every service", False, str(exc))

    healthy = {
        target["labels"]["job"]
        for target in body["data"]["activeTargets"]
        if target["health"] == "up"
    }
    missing = SCRAPED_JOBS - healthy
    return GateCheck(
        "prometheus scrapes every service",
        not missing,
        f"{len(SCRAPED_JOBS)} targets up" if not missing else f"missing {sorted(missing)}",
    )


def _run_scenario(scenario: str) -> GateCheck:
    """Inject one scenario, let it settle, and judge what it did.

    The baseline is read *before* the switch, not from the switch's own response —
    that response already reflects the new scenario, so comparing against it made
    traffic_surge measure 20 rps against 20 rps and never move.
    """
    before = _get(f"{SIMULATOR}/status")
    switched = _post(f"{SIMULATOR}/scenario", {"name": scenario})
    time.sleep(SETTLE_SECONDS)
    after = _get(f"{SIMULATOR}/status")
    # campaigns_changed is reported by the switch itself, not by the settle window.
    after["campaigns_changed"] = switched.get("campaigns_changed", 0)
    return evaluate_scenario(before, after, scenario)


def main() -> int:
    """Run the Level 0 gate against the live stack and print the scoreboard."""
    checks = _health_checks()
    checks.append(_targets_check())

    for scenario in ("steady", "error_burst", "traffic_surge", "bad_config_deploy"):
        checks.append(_run_scenario(scenario))
    checks.append(_run_scenario("budget_runaway"))

    # Leave the platform healthy: steady is also the rollback.
    _post(f"{SIMULATOR}/scenario", {"name": "steady"})

    passed, total = summarize(checks)
    width = max(len(check.name) for check in checks)
    print()
    for check in checks:
        print(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name.ljust(width)}  {check.detail}")
    print(f"\n  LEVEL 0 GATE: {passed}/{total} ({passed / total:.0%})\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
