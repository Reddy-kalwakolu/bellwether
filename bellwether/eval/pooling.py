# bellwether/eval/pooling.py
"""Build the judging pool so that no configuration can win by defining the key.

The pool is the union of the top results from *every* configuration that produces a
ranking, not just the retrieval ones. This is easy to get wrong and expensive when
you do: reranking reorders a window of the fused list, so a chunk sitting at rank 14
can be promoted into a reranked top-10 while being absent from a pool built only
from hybrid's top-10. It would then go unjudged, score zero, and silently penalise
the reranker for working.

Entries are shuffled and carry no score, rank, or originating mode. A judge who can
see which system found a chunk — or who reads the chunks in the order one system
ranked them — anchors on that system's opinion, and the answer key stops being
independent of the thing it measures.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bellwether.context.vectors import SearchHit
from bellwether.eval.gold import GoldQuery, GoldSet

DEFAULT_DEPTH = 10
DEFAULT_SEED = 8

# Rankings keyed query_id -> mode name -> that mode's hits.
Rankings = Mapping[str, Mapping[str, Sequence[SearchHit]]]


@dataclass(frozen=True)
class PoolEntry:
    """One thing to judge. Deliberately carries no hint of where it came from."""

    query_id: str
    chunk_id: str
    anchor: str | None
    source_path: str
    text: str


def build_pool(
    queries: Sequence[GoldQuery],
    rankings: Rankings,
    depth: int = DEFAULT_DEPTH,
    seed: int = DEFAULT_SEED,
) -> list[PoolEntry]:
    """The shuffled union of every configuration's top `depth`, per query."""
    entries: list[PoolEntry] = []
    for query in queries:
        by_mode = rankings.get(query.query_id, {})
        seen: dict[str, SearchHit] = {}
        for hits in by_mode.values():
            for hit in hits[:depth]:
                seen.setdefault(hit.chunk_id, hit)
        entries.extend(
            PoolEntry(
                query_id=query.query_id,
                chunk_id=hit.chunk_id,
                anchor=hit.anchor,
                source_path=hit.source_path,
                text=hit.text,
            )
            # Sorted before shuffling so the shuffle is the only source of order and
            # a seeded run is reproducible on any machine.
            for hit in [seen[key] for key in sorted(seen)]
        )

    random.Random(seed).shuffle(entries)
    return entries


def pool_coverage(goldset: GoldSet, rankings: Rankings, k: int = DEFAULT_DEPTH) -> float:
    """What share of retrieved chunks were actually judged.

    Pooling's known limitation, reported rather than hidden: a relevant chunk that no
    configuration retrieved was never judged and counts against nobody.
    """
    retrieved = 0
    judged = 0
    for query in goldset.queries:
        graded = set(query.judgements)
        for hits in rankings.get(query.query_id, {}).values():
            for hit in hits[:k]:
                retrieved += 1
                if hit.chunk_id in graded:
                    judged += 1
    if retrieved == 0:
        return 0.0
    return judged / retrieved
