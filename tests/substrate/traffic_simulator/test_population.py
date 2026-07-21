"""Traffic is random but reproducible: same seed, same run."""

from __future__ import annotations

from substrate.traffic_simulator.population import (
    CONTENT_RATINGS,
    COUNTRIES,
    DEVICE_TYPES,
    Population,
)


def test_a_member_is_drawn_from_the_declared_dimensions() -> None:
    member = Population(seed=1).member()
    assert member["member_id"].startswith("member-")
    assert member["country"] in COUNTRIES
    assert member["device_type"] in DEVICE_TYPES


def test_a_slot_is_valid_against_the_ad_decision_schema() -> None:
    slot = Population(seed=1).slot()
    assert 0 < int(slot["duration_seconds"]) <= 180  # type: ignore[call-overload]
    assert slot["content_rating"] in CONTENT_RATINGS
    assert isinstance(slot["content_categories"], list)
    assert slot["content_categories"]


def test_the_same_seed_produces_the_same_traffic() -> None:
    first = [Population(seed=7).ad_request() for _ in range(5)]
    second = [Population(seed=7).ad_request() for _ in range(5)]
    assert first == second


def test_different_seeds_produce_different_traffic() -> None:
    assert Population(seed=1).ad_request() != Population(seed=2).ad_request()


def test_an_ad_request_carries_a_member_and_a_slot() -> None:
    request = Population(seed=3).ad_request()
    assert set(request) == {"member", "slot"}


def test_members_repeat_so_frequency_capping_can_actually_bite() -> None:
    population = Population(seed=5)
    ids = {population.member()["member_id"] for _ in range(200)}
    # A small member pool is the point: an unbounded one would never hit a cap.
    assert len(ids) < 200
