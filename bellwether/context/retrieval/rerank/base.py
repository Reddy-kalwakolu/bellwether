"""One protocol, two rerankers, and a cost recorded when there is one.

Shaped deliberately like Day 7's `Embedder` — `spec`, `available()`, one verb —
because the lesson generalises: the engine is a parameter, not a commitment. A
reranker that cannot run says why, in words a human can act on, and the caller
falls back to the fused order rather than to nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from bellwether.context.embedders.base import UsageRecord
from bellwether.context.vectors import SearchHit


class RerankError(RuntimeError):
    """A reranker could not run. Always names the backend that failed."""


@dataclass(frozen=True)
class RerankerSpec:
    """What a reranker is, and what it costs — its row in the comparison."""

    name: str
    label: str
    hosted: bool
    notes: str


@dataclass(frozen=True)
class RerankResult:
    """A reordered ranking, and the bill for producing it if there was one."""

    hits: list[SearchHit]
    usage: UsageRecord | None


class Reranker(Protocol):
    """Everything the search service needs from a reranker."""

    @property
    def spec(self) -> RerankerSpec:
        """What this reranker is and what it costs."""
        ...

    def available(self) -> tuple[bool, str]:
        """Whether it can run, and if not, a reason a human can act on."""
        ...

    def rerank(self, query: str, hits: Sequence[SearchHit], limit: int) -> RerankResult:
        """Reorder `hits` by relevance to `query`, returning the best `limit`."""
        ...
