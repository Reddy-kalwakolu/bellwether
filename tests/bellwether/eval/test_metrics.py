"""Metrics checked against hand-computed values, not against our own output."""

from __future__ import annotations

import math

import pytest

from bellwether.eval.metrics import dcg, mrr, ndcg_at_k, recall_at_k

JUDGEMENTS = {"a": 2, "b": 1, "c": 0, "d": 2}


def test_dcg_of_a_perfect_two_is_two() -> None:
    assert dcg([2]) == pytest.approx(2.0)


def test_dcg_discounts_by_log2_of_position_plus_one() -> None:
    # gain 2 at rank 1 -> 2/log2(2) = 2 ; gain 1 at rank 2 -> 1/log2(3)
    assert dcg([2, 1]) == pytest.approx(2.0 + 1 / math.log2(3))


def test_ndcg_is_one_for_the_ideal_ordering() -> None:
    assert ndcg_at_k(["a", "d", "b", "c"], JUDGEMENTS, k=4) == pytest.approx(1.0)


def test_ndcg_is_lower_for_a_worse_ordering() -> None:
    ideal = ndcg_at_k(["a", "d", "b"], JUDGEMENTS, k=3)
    worse = ndcg_at_k(["b", "c", "a"], JUDGEMENTS, k=3)
    assert worse < ideal


def test_ndcg_is_zero_when_nothing_relevant_is_retrieved() -> None:
    assert ndcg_at_k(["c"], JUDGEMENTS, k=1) == 0.0


def test_ndcg_with_no_relevant_judgements_is_zero_not_a_crash() -> None:
    assert ndcg_at_k(["a"], {"a": 0}, k=1) == 0.0


def test_an_unjudged_id_counts_as_zero() -> None:
    assert ndcg_at_k(["unjudged"], JUDGEMENTS, k=1) == 0.0


def test_recall_counts_grade_one_and_above() -> None:
    # relevant set is {a, b, d}; retrieving a and b is 2/3
    assert recall_at_k(["a", "b", "c"], JUDGEMENTS, k=3) == pytest.approx(2 / 3)


def test_recall_is_one_when_everything_relevant_is_found() -> None:
    assert recall_at_k(["a", "b", "d"], JUDGEMENTS, k=3) == pytest.approx(1.0)


def test_recall_with_no_relevant_judgements_is_zero() -> None:
    assert recall_at_k(["a"], {"a": 0}, k=1) == 0.0


def test_mrr_is_one_over_the_first_relevant_rank() -> None:
    assert mrr(["c", "a"], JUDGEMENTS, k=2) == pytest.approx(0.5)


def test_mrr_is_zero_when_nothing_relevant_is_in_the_window() -> None:
    assert mrr(["c"], JUDGEMENTS, k=1) == 0.0


def test_k_truncates_the_ranking() -> None:
    assert mrr(["c", "a"], JUDGEMENTS, k=1) == 0.0


def test_an_empty_ranking_scores_zero_everywhere() -> None:
    assert ndcg_at_k([], JUDGEMENTS) == 0.0
    assert recall_at_k([], JUDGEMENTS) == 0.0
    assert mrr([], JUDGEMENTS) == 0.0


def test_ndcg_never_exceeds_one_on_a_duplicated_ranking() -> None:
    # A repeated chunk must not count its gain twice — the ideal ranking is built
    # from distinct judged chunks, so double-counting would push the ratio past 1.0.
    assert ndcg_at_k(["a", "a"], {"a": 2}, k=2) == pytest.approx(1.0)
