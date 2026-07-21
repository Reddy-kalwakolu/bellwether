"""The decision store holds per-day frequency counters and pacing spend."""

from __future__ import annotations

from datetime import date

from substrate.ad_decision_service.store import InMemoryDecisionStore, RedisDecisionStore

DAY = date(2026, 7, 21)
MEMBER = "member-1"
CAMPAIGN = "campaign-1"


def test_impression_count_starts_at_zero() -> None:
    store = InMemoryDecisionStore()
    assert store.impression_count(MEMBER, CAMPAIGN, DAY) == 0
    assert store.daily_spend_micros(CAMPAIGN, DAY) == 0


def test_recording_an_impression_advances_frequency_and_spend() -> None:
    store = InMemoryDecisionStore()
    store.record_impression(MEMBER, CAMPAIGN, DAY, price_micros=2_000)
    store.record_impression(MEMBER, CAMPAIGN, DAY, price_micros=2_000)
    assert store.impression_count(MEMBER, CAMPAIGN, DAY) == 2
    assert store.daily_spend_micros(CAMPAIGN, DAY) == 4_000


def test_counters_are_scoped_per_member_and_per_day() -> None:
    store = InMemoryDecisionStore()
    store.record_impression(MEMBER, CAMPAIGN, DAY, price_micros=2_000)
    assert store.impression_count("member-2", CAMPAIGN, DAY) == 0
    assert store.impression_count(MEMBER, CAMPAIGN, date(2026, 7, 22)) == 0
    # Spend is per campaign, not per member: a second member adds to the same budget.
    store.record_impression("member-2", CAMPAIGN, DAY, price_micros=2_000)
    assert store.daily_spend_micros(CAMPAIGN, DAY) == 4_000


class FakeRedis:
    """Minimal stand-in for the redis-py commands RedisDecisionStore uses."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expiries: list[tuple[str, int]] = []

    def get(self, key: str) -> str | None:
        """Return the stored counter as redis-py does, or None when unset."""
        return None if key not in self.values else str(self.values[key])

    def incrby(self, key: str, amount: int) -> int:
        """Advance a counter, creating it at zero first."""
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    def expire(self, key: str, seconds: int) -> None:
        """Record that a TTL was requested."""
        self.expiries.append((key, seconds))


def test_redis_store_uses_day_scoped_keys_and_sets_a_ttl() -> None:
    redis = FakeRedis()
    store = RedisDecisionStore(redis, ttl_seconds=172_800)
    store.record_impression(MEMBER, CAMPAIGN, DAY, price_micros=2_000)

    assert redis.values["freq:member-1:campaign-1:2026-07-21"] == 1
    assert redis.values["spend:campaign-1:2026-07-21"] == 2_000
    assert {key for key, _ in redis.expiries} == {
        "freq:member-1:campaign-1:2026-07-21",
        "spend:campaign-1:2026-07-21",
    }
    assert store.impression_count(MEMBER, CAMPAIGN, DAY) == 1
    assert store.daily_spend_micros(CAMPAIGN, DAY) == 2_000


def test_redis_store_reads_missing_keys_as_zero() -> None:
    store = RedisDecisionStore(FakeRedis(), ttl_seconds=172_800)
    assert store.impression_count(MEMBER, CAMPAIGN, DAY) == 0
    assert store.daily_spend_micros(CAMPAIGN, DAY) == 0
