"""The free reranker — real features, no network, and the CI default."""

from __future__ import annotations

from bellwether.context.retrieval.rerank import HeuristicReranker
from bellwether.context.vectors import SearchHit


def _hit(
    chunk_id: str,
    score: float,
    text: str = "some text",
    anchor: str | None = None,
    source_type: str = "devlog",
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=score,
        text=text,
        anchor=anchor,
        source_path=f"docs/{chunk_id}.md",
        source_type=source_type,
        component="docs",
        title=chunk_id,
    )


def test_is_always_available_and_says_so() -> None:
    available, reason = HeuristicReranker().available()
    assert available is True
    assert reason


def test_costs_nothing_and_reports_no_usage() -> None:
    result = HeuristicReranker().rerank("budget", [_hit("a", 1.0)], limit=1)
    assert result.usage is None


def test_promotes_an_exact_identifier_match_over_a_higher_ranked_near_miss() -> None:
    hits = [
        _hit("a", 0.9, text="budgets are discussed at length here"),
        _hit("b", 0.5, text="the budget_micros field is validated on write"),
    ]
    result = HeuristicReranker().rerank("budget_micros", hits, limit=2)
    assert result.hits[0].chunk_id == "b"


def test_promotes_an_anchor_match() -> None:
    hits = [
        _hit("a", 0.9, text="unrelated prose", anchor="Redis keys"),
        _hit("b", 0.6, text="unrelated prose", anchor="Frequency capping"),
    ]
    result = HeuristicReranker().rerank("frequency capping", hits, limit=2)
    assert result.hits[0].chunk_id == "b"


def test_a_why_question_prefers_an_adr_over_a_devlog() -> None:
    hits = [
        _hit("a", 0.8, text="we picked qdrant", source_type="devlog"),
        _hit("b", 0.8, text="we picked qdrant", source_type="adr"),
    ]
    result = HeuristicReranker().rerank("why did we pick qdrant", hits, limit=2)
    assert result.hits[0].chunk_id == "b"


def test_a_very_long_chunk_is_penalised_against_a_focused_one() -> None:
    focused = _hit("a", 0.7, text="budget_micros is enforced here")
    sprawling = _hit("b", 0.7, text="budget_micros " + ("filler " * 400))
    result = HeuristicReranker().rerank("budget_micros", [sprawling, focused], limit=2)
    assert result.hits[0].chunk_id == "a"


def test_an_ordinary_long_word_is_not_treated_as_an_identifier() -> None:
    # "observability" is thirteen characters of plain English. If length alone
    # qualified it, the largest boost in the table would fire on prose, inflating
    # the free baseline that Day 8 measures the LLM reranker against.
    hits = [
        _hit("a", 0.9, text="unrelated prose about caching"),
        _hit("b", 0.5, text="observability is covered in the runbook"),
    ]
    result = HeuristicReranker().rerank("observability", hits, limit=2)
    assert result.hits[0].chunk_id == "a"


def test_preserves_the_fused_order_when_no_feature_fires() -> None:
    hits = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
    result = HeuristicReranker().rerank("kubernetes helm chart", hits, limit=3)
    assert [hit.chunk_id for hit in result.hits] == ["a", "b", "c"]


def test_respects_the_limit() -> None:
    hits = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
    assert len(HeuristicReranker().rerank("anything", hits, limit=2).hits) == 2


def test_empty_input_reranks_to_empty() -> None:
    assert HeuristicReranker().rerank("anything", [], limit=5).hits == []


def test_is_deterministic() -> None:
    hits = [_hit("a", 0.5), _hit("b", 0.5), _hit("c", 0.5)]
    first = HeuristicReranker().rerank("budget", hits, limit=3).hits
    second = HeuristicReranker().rerank("budget", hits, limit=3).hits
    assert [hit.chunk_id for hit in first] == [hit.chunk_id for hit in second]
