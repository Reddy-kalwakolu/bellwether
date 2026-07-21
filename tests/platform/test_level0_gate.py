"""The Level 0 gate's decision logic, tested without a running stack."""

from __future__ import annotations

from level0_gate import GateCheck, evaluate_scenario, summarize


def test_an_error_burst_must_raise_rejected_requests() -> None:
    check = evaluate_scenario({"rejected": 0.0}, {"rejected": 12.0}, "error_burst")
    assert check.passed is True
    assert "12" in check.detail


def test_an_error_burst_that_rejects_nothing_fails() -> None:
    assert evaluate_scenario({"rejected": 3.0}, {"rejected": 3.0}, "error_burst").passed is False


def test_a_bad_config_deploy_must_drive_fill_rate_down() -> None:
    before = {"fills": 100.0, "ticks": 120.0}
    after = {"fills": 100.0, "ticks": 220.0}  # 100 more requests, zero more fills
    assert evaluate_scenario(before, after, "bad_config_deploy").passed is True


def test_a_bad_config_deploy_that_changes_nothing_fails() -> None:
    before = {"fills": 100.0, "ticks": 120.0}
    after = {"fills": 200.0, "ticks": 220.0}  # every request still fills
    assert evaluate_scenario(before, after, "bad_config_deploy").passed is False


def test_a_bad_config_deploy_cannot_pass_on_zero_traffic() -> None:
    """No requests means no evidence, which is not the same as a pass."""
    same = {"fills": 10.0, "ticks": 10.0}
    assert evaluate_scenario(same, same, "bad_config_deploy").passed is False


def test_a_budget_runaway_must_have_changed_configuration() -> None:
    assert evaluate_scenario({}, {"campaigns_changed": 1.0}, "budget_runaway").passed is True
    assert evaluate_scenario({}, {"campaigns_changed": 0.0}, "budget_runaway").passed is False


def test_a_traffic_surge_must_raise_the_request_rate() -> None:
    assert (
        evaluate_scenario(
            {"requests_per_second": 5.0}, {"requests_per_second": 50.0}, "traffic_surge"
        ).passed
        is True
    )
    assert (
        evaluate_scenario(
            {"requests_per_second": 5.0}, {"requests_per_second": 5.0}, "traffic_surge"
        ).passed
        is False
    )


def test_steady_must_actually_serve_ads() -> None:
    before = {"ticks": 0.0, "fills": 0.0}
    assert evaluate_scenario(before, {"ticks": 60.0, "fills": 45.0}, "steady").passed is True
    assert evaluate_scenario(before, {"ticks": 60.0, "fills": 0.0}, "steady").passed is False


def test_an_unknown_scenario_never_silently_passes() -> None:
    assert evaluate_scenario({}, {}, "not_a_scenario").passed is False


def test_the_summary_counts_passes() -> None:
    checks = [
        GateCheck("a", True, ""),
        GateCheck("b", False, ""),
        GateCheck("c", True, ""),
    ]
    assert summarize(checks) == (2, 3)
