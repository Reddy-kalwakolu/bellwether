# tests/bellwether/eval/test_pooling.py
"""The pool — shuffled, stripped of provenance, and drawn from every configuration."""

from __future__ import annotations

from datetime import UTC, datetime

from bellwether.context.vectors import SearchHit
from bellwether.eval.gold import Category, GoldQuery, GoldSet
from bellwether.eval.pooling import build_pool, pool_coverage

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _hit(chunk_id: str, score: float = 1.0) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=score,
        text=f"text of {chunk_id}",
        anchor=chunk_id,
        source_path=f"docs/{chunk_id}.md",
        source_type="adr",
        component="docs",
        title=chunk_id,
    )


def _query(query_id: str = "q001", judgements: dict[str, int] | None = None) -> GoldQuery:
    return GoldQuery(
        query_id=query_id,
        text="anything",
        category=Category.CONCEPTUAL,
        judgements=judgements or {"a": 2},
    )


def test_pools_the_union_of_every_configuration() -> None:
    rankings = {
        "q001": {
            "lexical": [_hit("a"), _hit("b")],
            "dense": [_hit("c")],
            "hybrid-llm": [_hit("d")],
        }
    }
    entries = build_pool([_query()], rankings, depth=10)
    assert {entry.chunk_id for entry in entries} == {"a", "b", "c", "d"}


def test_a_chunk_seen_by_two_configurations_appears_once() -> None:
    rankings = {"q001": {"lexical": [_hit("a")], "dense": [_hit("a")]}}
    assert len(build_pool([_query()], rankings, depth=10)) == 1


def test_depth_truncates_each_configuration_before_pooling() -> None:
    rankings = {"q001": {"lexical": [_hit("a"), _hit("b"), _hit("c")]}}
    entries = build_pool([_query()], rankings, depth=2)
    assert {entry.chunk_id for entry in entries} == {"a", "b"}


def test_the_pool_entry_carries_no_hint_of_which_system_found_it() -> None:
    rankings = {"q001": {"lexical": [_hit("a")]}}
    entry = build_pool([_query()], rankings, depth=10)[0]
    assert not hasattr(entry, "mode")
    assert not hasattr(entry, "score")


def test_the_shuffle_is_seeded_so_judging_is_reproducible() -> None:
    rankings = {"q001": {"lexical": [_hit(letter) for letter in "abcdefgh"]}}
    first = [entry.chunk_id for entry in build_pool([_query()], rankings, depth=10, seed=8)]
    second = [entry.chunk_id for entry in build_pool([_query()], rankings, depth=10, seed=8)]
    assert first == second


def test_the_shuffle_does_not_preserve_retrieval_order() -> None:
    # If the pool arrived in rank order, a judge would anchor on the first system's
    # opinion — which is exactly the bias pooling exists to remove.
    ordered = [_hit(f"c{index:02d}") for index in range(20)]
    entries = build_pool([_query()], {"q001": {"lexical": ordered}}, depth=20, seed=8)
    assert [entry.chunk_id for entry in entries] != [hit.chunk_id for hit in ordered]


def test_coverage_is_one_when_every_retrieved_chunk_was_judged() -> None:
    goldset = GoldSet(
        version="1",
        created_at=NOW,
        notes="",
        queries=[_query(judgements={"a": 2, "b": 0})],
    )
    rankings = {"q001": {"hybrid": [_hit("a"), _hit("b")]}}
    assert pool_coverage(goldset, rankings) == 1.0


def test_coverage_falls_when_a_configuration_surfaces_an_unjudged_chunk() -> None:
    goldset = GoldSet(version="1", created_at=NOW, notes="", queries=[_query(judgements={"a": 2})])
    rankings = {"q001": {"hybrid": [_hit("a"), _hit("unjudged")]}}
    assert pool_coverage(goldset, rankings) == 0.5
