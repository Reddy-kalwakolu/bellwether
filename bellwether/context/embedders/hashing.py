"""The engine the test suite uses: no downloads, no network, no key, no drift.

This is a hashed bag-of-words with sign hashing, projected to 256 dimensions and
L2-normalised. It is a real lexical embedder, not a stub — two chunks sharing
vocabulary genuinely score higher against each other than two that do not — which is
what lets the suite assert retrieval behaviour without a model in CI.

What it cannot do is match on meaning. "frequency capping" and "how often a member
sees an ad" share no tokens and score near zero. That gap is exactly what the hosted
engines are being paid for, and putting a number on it is the point of the desk.
"""

from __future__ import annotations

import hashlib
import math
import re
import time

from bellwether.context.embedders.base import EmbeddingResult, EngineSpec, UsageRecord

DIMENSIONS = 256
_TOKEN = re.compile(r"[a-z0-9_]+")

SPEC = EngineSpec(
    name="hashing",
    label="HASHING",
    dimensions=DIMENSIONS,
    hosted=False,
    cost_per_million_tokens=0.0,
    mteb_retrieval=None,
    notes="Deterministic lexical baseline. No dependencies, no network — the CI engine.",
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens. Identifier-friendly, so `budget_micros` stays one token."""
    return _TOKEN.findall(text.lower())


def _vector(text: str) -> list[float]:
    """Hash every token into a bucket with a sign, then normalise."""
    accumulator = [0.0] * DIMENSIONS
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        bucket = value % DIMENSIONS
        # The sign bit spreads collisions instead of letting them all add up.
        accumulator[bucket] += 1.0 if (value >> 8) & 1 else -1.0

    norm = math.sqrt(sum(component * component for component in accumulator))
    if norm == 0.0:
        return accumulator
    return [component / norm for component in accumulator]


class HashingEmbedder:
    """A deterministic lexical embedder with no dependencies at all."""

    @property
    def spec(self) -> EngineSpec:
        """What this engine is and what it costs."""
        return SPEC

    def available(self) -> tuple[bool, str]:
        """Always available. That is the whole reason it exists."""
        return True, "built in"

    def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed every text, in order, reporting what it consumed."""
        started = time.perf_counter()
        vectors = [_vector(text) for text in texts]
        tokens = sum(len(tokenize(text)) for text in texts)
        return EmbeddingResult(
            vectors=vectors,
            usage=UsageRecord(
                engine=SPEC.name,
                texts=len(texts),
                tokens=tokens,
                cost_usd=0.0,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
        )
