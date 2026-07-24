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

# How fast the incoming rank decays into the base score.
#
# The base must be scale-free. Using `hit.score` directly would make the boosts
# meaningless or overwhelming depending on which mode fed us — RRF scores sit near
# 0.016, BM25 scores reach 20 — which is the exact scale-mixing problem ADR-0009
# rejects. So rank position is the only input.
#
# 0.05 sets the exchange rate: one rank position is worth about 0.05, so a single
# exact-identifier match (0.5) is worth roughly ten places. That is the intended
# strength — an identifier hit should climb past near-misses, not merely nudge.
RANK_STEP = 0.05


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
        # A separator is the only honest signal that a term is a name rather than a
        # word. A length threshold looks tempting — it would catch `addecisionservice`,
        # whose capitals tokenization has already discarded — but it also hands the
        # largest boost in the table to "frequency", "kubernetes" and "observability".
        # That would inflate the free baseline on ordinary prose, and this day exists
        # to compare that baseline against an LLM honestly. camelCase wholes still
        # reach the top via BM25's IDF; they just do not collect a bonus here.
        identifiers = {term for term in terms if any(mark in term for mark in "_-./")}
        wants_why = bool(WHY_WORDS & set(terms))

        scored: list[tuple[float, int, SearchHit]] = []
        for position, hit in enumerate(hits):
            # Base keeps the incoming order meaningful: a reranker that discards the
            # retriever's opinion entirely is a retriever, not a reranker. Decays
            # with rank rather than with the incoming score, so the boosts below
            # mean the same thing whichever mode produced these hits — and stays
            # positive and monotonic however deep the candidate list runs.
            score = 1.0 / (1.0 + position * RANK_STEP)
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
        if anchor and any(term in anchor for term in terms):
            boost += weights.anchor

        if wants_why and hit.source_type in WHY_PREFERRED:
            boost += weights.source_prior

        if len(hit.text) > SPRAWL_CHARS:
            boost -= weights.length_penalty

        return boost
