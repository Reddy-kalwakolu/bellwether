"""Turning rankings into the table the day publishes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bellwether.context.vectors import SearchHit
from bellwether.eval.gold import Category, GoldQuery, GoldSet
from bellwether.eval.report import evaluate, evaluate_category, format_results

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _hit(chunk_id: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=1.0,
        text="text",
        anchor=chunk_id,
        source_path=f"docs/{chunk_id}.md",
        source_type="adr",
        component="docs",
        title=chunk_id,
    )


def _goldset() -> GoldSet:
    return GoldSet(
        version="1",
        created_at=NOW,
        notes="",
        queries=[
            GoldQuery(
                query_id="q1",
                text="identifier question",
                category=Category.IDENTIFIER,
                judgements={"a": 2, "b": 0},
            ),
            GoldQuery(
                query_id="q2",
                text="conceptual question",
                category=Category.CONCEPTUAL,
                judgements={"c": 2, "d": 1},
            ),
        ],
    )


def test_a_perfect_configuration_scores_one() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}, "q2": {"lexical": [_hit("c"), _hit("d")]}}
    result = evaluate(_goldset(), rankings)[0]
    assert result.ndcg == pytest.approx(1.0)
    assert result.recall == pytest.approx(1.0)


def test_a_configuration_that_finds_nothing_scores_zero() -> None:
    rankings = {"q1": {"lexical": [_hit("b")]}, "q2": {"lexical": [_hit("z")]}}
    result = evaluate(_goldset(), rankings)[0]
    assert result.ndcg == 0.0
    assert result.recall == 0.0


def test_scores_are_averaged_over_the_queries() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}, "q2": {"lexical": [_hit("z")]}}
    result = evaluate(_goldset(), rankings)[0]
    assert result.ndcg == pytest.approx(0.5)
    assert result.queries == 2


def test_one_result_per_configuration() -> None:
    rankings = {
        "q1": {"lexical": [_hit("a")], "hybrid": [_hit("a")]},
        "q2": {"lexical": [_hit("c")], "hybrid": [_hit("c")]},
    }
    assert {result.mode for result in evaluate(_goldset(), rankings)} == {"lexical", "hybrid"}


def test_category_breakdown_scores_only_that_category() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}, "q2": {"lexical": [_hit("z")]}}
    identifier = evaluate_category(_goldset(), rankings, Category.IDENTIFIER)[0]
    conceptual = evaluate_category(_goldset(), rankings, Category.CONCEPTUAL)[0]
    assert identifier.ndcg == pytest.approx(1.0)
    assert conceptual.ndcg == 0.0


def test_cost_and_latency_are_carried_through_when_supplied() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}, "q2": {"lexical": [_hit("c")]}}
    result = evaluate(_goldset(), rankings, latencies={"lexical": 12.5}, costs={"lexical": 0.0123})[
        0
    ]
    assert result.latency_ms == pytest.approx(12.5)
    assert result.cost_usd == pytest.approx(0.0123)


def test_the_table_names_every_configuration_and_metric() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}, "q2": {"lexical": [_hit("c")]}}
    table = format_results(evaluate(_goldset(), rankings), title="All queries")
    assert "All queries" in table
    assert "lexical" in table
    assert "nDCG@10" in table


def test_an_unranked_query_scores_zero_rather_than_crashing() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}}
    result = evaluate(_goldset(), rankings)[0]
    assert result.queries == 2
    assert result.ndcg == pytest.approx(0.5)
