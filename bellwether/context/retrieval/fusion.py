"""Combine a dense ranking and a lexical one without picking the winner in advance.

Cosine similarity lives in [-1, 1]. BM25 is unbounded and routinely reaches 20.
Adding them requires normalising them first, and every normalisation scheme is a
tuning knob — one that can be turned, consciously or not, until the system you were
hoping to promote comes out ahead. On a day whose entire deliverable is an honest
comparison, that is not a knob worth having.

Reciprocal Rank Fusion uses rank position only. It never sees the scores. It has one
constant, k=60, which is the value from the original paper and is not tuned here.

`weighted_fusion` is the normalising alternative, kept so the choice is measured
rather than asserted. It gets its own row in the comparison table.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from bellwether.context.vectors import SearchHit

# From Cormack et al. 2009. Published default, deliberately not tuned against the
# gold set — a fusion constant fitted on the eval set is the eval set's constant.
RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[SearchHit]],
    k: int = RRF_K,
    limit: int = 10,
) -> list[SearchHit]:
    """Fuse rankings on rank position alone: sum of 1 / (k + rank)."""
    scores: dict[str, float] = {}
    seen: dict[str, SearchHit] = {}

    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1 / (k + rank)
            seen.setdefault(hit.chunk_id, hit)

    return _top(scores, seen, limit)


def weighted_fusion(
    dense: Sequence[SearchHit],
    lexical: Sequence[SearchHit],
    alpha: float = 0.5,
    limit: int = 10,
) -> list[SearchHit]:
    """Min-max normalise each side, then `alpha * dense + (1 - alpha) * lexical`."""
    scores: dict[str, float] = {}
    seen: dict[str, SearchHit] = {}

    for ranking, weight in ((dense, alpha), (lexical, 1 - alpha)):
        for chunk_id, normalized in _normalize(ranking).items():
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight * normalized
        for hit in ranking:
            seen.setdefault(hit.chunk_id, hit)

    return _top(scores, seen, limit)


def _normalize(ranking: Sequence[SearchHit]) -> dict[str, float]:
    """Min-max to [0, 1]. A single hit, or an all-equal ranking, normalises to 1.0."""
    if not ranking:
        return {}
    values = [hit.score for hit in ranking]
    lowest = min(values)
    highest = max(values)
    if highest == lowest:
        return {hit.chunk_id: 1.0 for hit in ranking}
    span = highest - lowest
    return {hit.chunk_id: (hit.score - lowest) / span for hit in ranking}


def _top(scores: dict[str, float], seen: dict[str, SearchHit], limit: int) -> list[SearchHit]:
    """Highest score first, ties broken on chunk_id so runs are reproducible."""
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [dataclasses.replace(seen[chunk_id], score=score) for chunk_id, score in ordered[:limit]]
