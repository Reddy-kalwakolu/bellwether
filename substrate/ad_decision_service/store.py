"""Per-day decision state: frequency counters and pacing spend.

Postgres is the system of record for campaign *configuration*. These counters are
something else — high-write, per-member, and worthless after midnight. They live in
Redis behind a protocol so tests can run against an in-memory double (ADR-0003).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

# Two days, so a counter written just before midnight survives long enough to be read
# by the requests that follow it.
DEFAULT_TTL_SECONDS = 172_800


def frequency_key(member_id: str, campaign_id: str, day: date) -> str:
    """Key holding how many times this member saw this campaign today."""
    return f"freq:{member_id}:{campaign_id}:{day.isoformat()}"


def spend_key(campaign_id: str, day: date) -> str:
    """Key holding how much this campaign has spent today, in micros."""
    return f"spend:{campaign_id}:{day.isoformat()}"


class DecisionStore(Protocol):
    """The state the decision path reads and writes on every served impression."""

    def impression_count(self, member_id: str, campaign_id: str, day: date) -> int:
        """How many impressions of `campaign_id` this member has seen on `day`."""
        ...

    def daily_spend_micros(self, campaign_id: str, day: date) -> int:
        """How much `campaign_id` has spent on `day`, in micros."""
        ...

    def record_impression(
        self, member_id: str, campaign_id: str, day: date, price_micros: int
    ) -> None:
        """Advance both the frequency counter and the day's spend."""
        ...


class InMemoryDecisionStore:
    """Process-local store, used by the test suite so it needs no infrastructure."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def impression_count(self, member_id: str, campaign_id: str, day: date) -> int:
        """How many impressions of `campaign_id` this member has seen on `day`."""
        return self._counters.get(frequency_key(member_id, campaign_id, day), 0)

    def daily_spend_micros(self, campaign_id: str, day: date) -> int:
        """How much `campaign_id` has spent on `day`, in micros."""
        return self._counters.get(spend_key(campaign_id, day), 0)

    def record_impression(
        self, member_id: str, campaign_id: str, day: date, price_micros: int
    ) -> None:
        """Advance both the frequency counter and the day's spend."""
        freq = frequency_key(member_id, campaign_id, day)
        spend = spend_key(campaign_id, day)
        self._counters[freq] = self._counters.get(freq, 0) + 1
        self._counters[spend] = self._counters.get(spend, 0) + price_micros


class RedisClient(Protocol):
    """The three Redis commands this service needs. Keeps the seam narrow."""

    def get(self, key: str) -> str | None: ...

    def incrby(self, key: str, amount: int) -> int: ...

    def expire(self, key: str, seconds: int) -> None: ...


class RedisDecisionStore:
    """Production store: day-scoped keys that expire themselves."""

    def __init__(self, redis: RedisClient, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    def _read_int(self, key: str) -> int:
        """Read a counter, treating an unset key as zero."""
        raw = self._redis.get(key)
        return 0 if raw is None else int(raw)

    def _bump(self, key: str, amount: int) -> None:
        """Advance a counter and refresh its TTL."""
        self._redis.incrby(key, amount)
        self._redis.expire(key, self._ttl_seconds)

    def impression_count(self, member_id: str, campaign_id: str, day: date) -> int:
        """How many impressions of `campaign_id` this member has seen on `day`."""
        return self._read_int(frequency_key(member_id, campaign_id, day))

    def daily_spend_micros(self, campaign_id: str, day: date) -> int:
        """How much `campaign_id` has spent on `day`, in micros."""
        return self._read_int(spend_key(campaign_id, day))

    def record_impression(
        self, member_id: str, campaign_id: str, day: date, price_micros: int
    ) -> None:
        """Advance both the frequency counter and the day's spend."""
        self._bump(frequency_key(member_id, campaign_id, day), 1)
        self._bump(spend_key(campaign_id, day), price_micros)
