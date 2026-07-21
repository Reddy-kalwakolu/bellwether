"""The audience the simulator pretends to serve.

Seeded on purpose. A reproducible traffic pattern means a demo can be re-run and
an incident can be replayed — which matters more here than statistical realism,
because Level 3's ops agents will be evaluated against runs that must be repeatable.
"""

from __future__ import annotations

import random

COUNTRIES = ("US", "CA", "GB", "DE", "BR")
DEVICE_TYPES = ("tv", "mobile", "tablet", "desktop")
CONTENT_RATINGS = ("TV-G", "TV-14", "TV-MA")
CONTENT_CATEGORIES = ("drama", "comedy", "documentary", "news", "true-crime", "sports")
SLOT_DURATIONS = (15, 30, 60)

# A bounded member pool, so the same member returns often enough that frequency
# capping actually engages. An unbounded pool would never hit a cap and the
# frequency_capped band would stay flat at zero forever.
MEMBER_POOL_SIZE = 120


class Population:
    """Draws members and slots from a fixed seed."""

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def member(self) -> dict[str, str]:
        """One viewer, as the decision path sees them."""
        return {
            "member_id": f"member-{self._random.randrange(MEMBER_POOL_SIZE):04d}",
            "country": self._random.choice(COUNTRIES),
            "device_type": self._random.choice(DEVICE_TYPES),
        }

    def slot(self) -> dict[str, object]:
        """One ad break, and the content surrounding it."""
        return {
            "slot_id": f"slot-{self._random.randrange(1_000):03d}",
            "duration_seconds": self._random.choice(SLOT_DURATIONS),
            "content_rating": self._random.choice(CONTENT_RATINGS),
            "content_categories": self._random.sample(CONTENT_CATEGORIES, k=2),
        }

    def ad_request(self) -> dict[str, object]:
        """One opportunity to serve an ad."""
        return {"member": self.member(), "slot": self.slot()}
