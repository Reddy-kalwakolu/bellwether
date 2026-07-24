# tests/bellwether/eval/test_gold.py
"""The answer key — validated on load, because a bad one silently skews everything."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from bellwether.eval.gold import Category, GoldQuery, GoldSet, load_gold_set, save_gold_set

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _query(query_id: str = "q001") -> GoldQuery:
    return GoldQuery(
        query_id=query_id,
        text="where is budget_micros enforced",
        category=Category.IDENTIFIER,
        judgements={"a#0001": 2, "a#0002": 0},
    )


def test_relevant_returns_grades_at_or_above_one() -> None:
    goldset = GoldSet(version="1", created_at=NOW, notes="", queries=[_query()])
    assert goldset.relevant("q001") == {"a#0001"}


def test_a_grade_outside_zero_to_two_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GoldQuery(
            query_id="q001",
            text="x",
            category=Category.IDENTIFIER,
            judgements={"a#0001": 3},
        )


def test_a_query_with_no_relevant_chunk_is_rejected() -> None:
    # An unanswerable query scores every configuration zero and moves the mean for
    # no reason. Catch it at load, not in the published table.
    with pytest.raises(ValidationError):
        GoldQuery(
            query_id="q001",
            text="x",
            category=Category.IDENTIFIER,
            judgements={"a#0001": 0},
        )


def test_duplicate_query_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        GoldSet(version="1", created_at=NOW, notes="", queries=[_query(), _query()])


def test_round_trips_through_disk(tmp_path: Path) -> None:
    goldset = GoldSet(version="1", created_at=NOW, notes="n", queries=[_query()])
    path = tmp_path / "gold.json"
    save_gold_set(goldset, path)
    assert load_gold_set(path).queries[0].judgements == {"a#0001": 2, "a#0002": 0}


def test_categories_are_the_three_the_report_breaks_out() -> None:
    assert {member.value for member in Category} == {
        "identifier",
        "conceptual",
        "cross_document",
    }
