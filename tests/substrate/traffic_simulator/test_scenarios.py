"""The five failure modes, as data."""

from __future__ import annotations

import pytest

from substrate.traffic_simulator.population import Population
from substrate.traffic_simulator.scenarios import SCENARIOS, corrupt, get


def test_every_scenario_is_named_and_summarized() -> None:
    assert set(SCENARIOS) == {
        "steady",
        "error_burst",
        "traffic_surge",
        "bad_config_deploy",
        "budget_runaway",
    }
    for name, scenario in SCENARIOS.items():
        assert scenario.name == name
        assert scenario.summary


def test_steady_is_the_baseline_and_also_the_rollback() -> None:
    """Injected failures change real config, so returning to steady must undo it."""
    steady = get("steady")
    assert steady.rate_multiplier == 1.0
    assert steady.malformed_fraction == 0.0
    assert steady.config_mutation == "restore_seed_config"


def test_error_burst_sends_malformed_requests_without_touching_config() -> None:
    burst = get("error_burst")
    assert burst.malformed_fraction > 0
    assert burst.config_mutation is None


def test_traffic_surge_only_raises_the_rate() -> None:
    surge = get("traffic_surge")
    assert surge.rate_multiplier > 1
    assert surge.malformed_fraction == 0.0
    assert surge.config_mutation is None


def test_the_config_scenarios_name_a_real_mutation() -> None:
    assert get("bad_config_deploy").config_mutation == "retarget_all_campaigns"
    assert get("budget_runaway").config_mutation == "inflate_one_daily_budget"


def test_an_unknown_scenario_is_a_key_error() -> None:
    with pytest.raises(KeyError):
        get("chaos_monkey")


def test_a_corrupted_request_violates_the_ad_decision_schema() -> None:
    corrupted = corrupt(Population(seed=1).ad_request())
    slot = corrupted["slot"]
    assert isinstance(slot, dict)
    # duration_seconds is `gt=0` on the Slot model, so zero is a guaranteed 422.
    assert slot["duration_seconds"] == 0


def test_corrupting_a_request_does_not_mutate_the_original() -> None:
    original = Population(seed=1).ad_request()
    before = str(original)
    corrupt(original)
    assert str(original) == before
