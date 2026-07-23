"""A reranker with no model, no network and no bill — and real features.

This is not a stub standing in for the LLM reranker. It is a defensible baseline
that the LLM has to beat, built from four signals that a retrieval engineer would
recognise: does the chunk contain the identifier that was asked for, does its anchor
name the thing, is its document type the kind that answers this kind of question,
and is the match incidental to a wall of text.

Because it is real, the test suite can assert rerank *behaviour* without a model —
which is what keeps the whole suite hermetic.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass

from bellwether.context.retrieval.rerank.base import RerankerSpec, RerankResult
from bellwether.context.retrieval.tokenize import tokenize
from bellwether.context.vectors import SearchHit

# A question starting with one of these is asking for a decision and its reasoning,
# which is what an ADR is and what a devlog only incidentally contains.
WHY_WORDS = frozenset({"why", "rationale", "reason", "decide", "decided", "chose", "chosen"})

WHY_PREFERRED = frozenset({"adr", "spec", "standards"})

# Long enough that a single term match says little about what the chunk is about.
SPRAWL_CHARS = 1200


@dataclass(frozen=True)
class HeuristicWeights:
    """How much each signal moves a chunk. Published, not fitted to the gold set."""

    identifier: float = 0.5
    anchor: float = 0.3
    source_prior: float = 0.15
    length_penalty: float = 0.1


class HeuristicReranker:
    """Free, deterministic, offline. The CI default and the LLM's baseline."""

    def __init__(self, weights: HeuristicWeights | None = None) -> None:
        self.weights = weights or HeuristicWeights()

    @property
    def spec(self) -> RerankerSpec:
        """What this reranker is and what it costs."""
        return RerankerSpec(
            name="heuristic",
            label="Heuristic",
            hosted=False,
            notes="identifier, anchor, source-type prior, length penalty",
        )

    def available(self) -> tuple[bool, str]:
        """Always available — no key, no package, no network."""
        return True, "no dependencies"

    def rerank(self, query: str, hits: Sequence[SearchHit], limit: int) -> RerankResult:
        """Reorder `hits` by the four signals, preserving fused order on ties."""
        if not hits:
            return RerankResult(hits=[], usage=None)

        terms = tokenize(query)
        identifiers = {term for term in terms if "_" in term or "-" in term or len(term) >= 8}
        wants_why = bool(WHY_WORDS & set(terms))

        scored: list[tuple[float, int, SearchHit]] = []
        for position, hit in enumerate(hits):
            # Base keeps the incoming order meaningful: a reranker that discards the
            # retriever's opinion entirely is a retriever, not a reranker.
            score = hit.score
            score += self._boost(hit, terms, identifiers, wants_why)
            scored.append((score, position, hit))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return RerankResult(
            hits=[dataclasses.replace(hit, score=score) for score, _, hit in scored[:limit]],
            usage=None,
        )

    def _boost(
        self,
        hit: SearchHit,
        terms: Sequence[str],
        identifiers: set[str],
        wants_why: bool,
    ) -> float:
        """How far the four signals move this one chunk."""
        weights = self.weights
        text = hit.text.lower()
        boost = 0.0

        if identifiers and any(identifier in text for identifier in identifiers):
            boost += weights.identifier

        anchor = (hit.anchor or "").lower()
        matching_terms = sum(1 for term in terms if term in anchor)
        if matching_terms > 0:
            boost += weights.anchor * matching_terms

        if wants_why and hit.source_type in WHY_PREFERRED:
            boost += weights.source_prior

        if len(hit.text) > SPRAWL_CHARS:
            boost -= weights.length_penalty

        return boost
