"""Combining two rankings without inventing a normalisation that picks the winner."""

from __future__ import annotations

from bellwether.context.retrieval.fusion import (
    RRF_K,
    reciprocal_rank_fusion,
    weighted_fusion,
)
from bellwether.context.vectors import SearchHit


def _hit(chunk_id: str, score: float) -> SearchHit:
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


def test_a_chunk_ranked_well_by_both_beats_one_ranked_well_by_either() -> None:
    dense = [_hit("a", 0.9), _hit("b", 0.8)]
    lexical = [_hit("b", 12.0), _hit("c", 9.0)]
    fused = reciprocal_rank_fusion([dense, lexical], limit=3)
    assert fused[0].chunk_id == "b"


def test_fused_score_is_the_rrf_sum_not_the_original() -> None:
    dense = [_hit("a", 0.9)]
    lexical = [_hit("a", 250.0)]
    fused = reciprocal_rank_fusion([dense, lexical], limit=1)
    assert fused[0].score == 2 / (RRF_K + 1)


def test_ignores_the_magnitude_of_the_input_scores() -> None:
    # The whole point: BM25 at 250.0 must not outvote cosine at 0.9 by being bigger.
    small = reciprocal_rank_fusion([[_hit("a", 0.01)], [_hit("b", 0.02)]], limit=2)
    huge = reciprocal_rank_fusion([[_hit("a", 1000.0)], [_hit("b", 2000.0)]], limit=2)
    assert [hit.chunk_id for hit in small] == [hit.chunk_id for hit in huge]


def test_preserves_provenance_from_the_first_list_that_saw_the_chunk() -> None:
    fused = reciprocal_rank_fusion([[_hit("a", 0.9)], [_hit("a", 3.0)]], limit=1)
    assert fused[0].anchor == "a"
    assert fused[0].source_path == "docs/a.md"


def test_empty_rankings_fuse_to_nothing() -> None:
    assert reciprocal_rank_fusion([], limit=5) == []
    assert reciprocal_rank_fusion([[], []], limit=5) == []


def test_one_ranking_fuses_to_itself_in_order() -> None:
    fused = reciprocal_rank_fusion([[_hit("a", 0.9), _hit("b", 0.5)]], limit=2)
    assert [hit.chunk_id for hit in fused] == ["a", "b"]


def test_respects_the_limit() -> None:
    dense = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
    assert len(reciprocal_rank_fusion([dense], limit=2)) == 2


def test_ties_break_on_chunk_id_not_dict_order() -> None:
    fused = reciprocal_rank_fusion([[_hit("z", 0.5)], [_hit("a", 0.5)]], limit=2)
    assert [hit.chunk_id for hit in fused] == ["a", "z"]


def test_weighted_fusion_at_alpha_one_is_dense_only() -> None:
    dense = [_hit("a", 0.9), _hit("b", 0.1)]
    lexical = [_hit("c", 20.0)]
    fused = weighted_fusion(dense, lexical, alpha=1.0, limit=1)
    assert fused[0].chunk_id == "a"


def test_weighted_fusion_at_alpha_zero_is_lexical_only() -> None:
    dense = [_hit("a", 0.9)]
    lexical = [_hit("c", 20.0), _hit("d", 1.0)]
    fused = weighted_fusion(dense, lexical, alpha=0.0, limit=1)
    assert fused[0].chunk_id == "c"


def test_weighted_fusion_survives_a_single_element_list() -> None:
    # min == max, so the normaliser divides by zero unless it is guarded.
    fused = weighted_fusion([_hit("a", 0.9)], [_hit("b", 5.0)], alpha=0.5, limit=2)
    assert {hit.chunk_id for hit in fused} == {"a", "b"}
