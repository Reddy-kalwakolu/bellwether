"""What "better retrieval" means, as arithmetic rather than as an adjective.

Three metrics because each answers a different question a real user has. nDCG@10
asks whether the good answers are near the top and grades partial answers as
partial. recall@10 asks the blunter question — did the answer make the cut at all.
MRR asks how far a human scrolls before the first useful thing.

Deliberately pure: these take ranked ids and a judgement map, and know nothing about
`SearchHit`, embeddings, or which configuration produced the ranking. That is what
lets them be checked against values computed by hand rather than against our own
output, which would only prove the code agrees with itself.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

# Grades are 0 (irrelevant), 1 (partially answers), 2 (fully answers). Anything at
# or above 1 counts as relevant for the set-based metrics.
RELEVANT_FROM = 1

DEFAULT_K = 10


def dcg(gains: Sequence[int]) -> float:
    """Discounted cumulative gain: each gain divided by log2 of its rank plus one."""
    return sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))


def _first_seen(ranked_ids: Sequence[str]) -> list[str]:
    """The ranking with later repeats of a chunk removed, order preserved.

    nDCG is defined over distinct documents, and the ideal ranking is built from the
    distinct judged chunks — so a duplicate in the actual ranking counts a gain the
    ideal never can, which can push the ratio above 1.0. Fusion already dedupes by
    chunk_id, so this should not fire in practice; keeping the metric correct for the
    case it can't see is cheaper than trusting every future caller to.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for chunk_id in ranked_ids:
        if chunk_id not in seen:
            seen.add(chunk_id)
            unique.append(chunk_id)
    return unique


def ndcg_at_k(
    ranked_ids: Sequence[str], judgements: Mapping[str, int], k: int = DEFAULT_K
) -> float:
    """nDCG@k — graded and rank-aware. Unjudged ids score zero, duplicates count once."""
    gains = [judgements.get(chunk_id, 0) for chunk_id in _first_seen(ranked_ids)[:k]]
    ideal = sorted(judgements.values(), reverse=True)[:k]
    best = dcg(ideal)
    if best == 0:
        return 0.0
    return dcg(gains) / best


def recall_at_k(
    ranked_ids: Sequence[str], judgements: Mapping[str, int], k: int = DEFAULT_K
) -> float:
    """What share of the relevant chunks appear in the top k."""
    relevant = {chunk_id for chunk_id, grade in judgements.items() if grade >= RELEVANT_FROM}
    if not relevant:
        return 0.0
    found = relevant & set(ranked_ids[:k])
    return len(found) / len(relevant)


def mrr(ranked_ids: Sequence[str], judgements: Mapping[str, int], k: int = DEFAULT_K) -> float:
    """One over the rank of the first relevant chunk, or zero if there is none."""
    for rank, chunk_id in enumerate(ranked_ids[:k], start=1):
        if judgements.get(chunk_id, 0) >= RELEVANT_FROM:
            return 1 / rank
    return 0.0
