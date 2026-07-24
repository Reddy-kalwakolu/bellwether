# bellwether/eval/gold.py
"""The answer key, and the validation that keeps it honest.

Two rules are enforced at load rather than trusted. A grade outside 0-2 is a typo
that would quietly distort nDCG. A query with nothing relevant scores every
configuration zero and drags the mean down identically for all of them, which looks
like data and is actually noise.

The file lives under `data/gold/` — committed via a `.gitignore` negation, and never
ingested, because `discovery.py` excludes every path with a `data` segment. Both
properties matter: an answer key nobody can check is not evidence, and an answer key
inside the searchable corpus is contamination.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, field_validator, model_validator

# Grades: 0 irrelevant, 1 partially answers or is needed context, 2 fully answers.
MIN_GRADE = 0
MAX_GRADE = 2
RELEVANT_FROM = 1


class Category(StrEnum):
    """The three question shapes the comparison reports separately."""

    IDENTIFIER = "identifier"
    CONCEPTUAL = "conceptual"
    CROSS_DOCUMENT = "cross_document"


class GoldQuery(BaseModel):
    """One question, and every chunk that was judged against it."""

    query_id: str
    text: str
    category: Category
    judgements: dict[str, int]

    @field_validator("judgements")
    @classmethod
    def _grades_in_range(cls, value: dict[str, int]) -> dict[str, int]:
        """Every grade must be 0, 1 or 2."""
        for chunk_id, grade in value.items():
            if grade < MIN_GRADE or grade > MAX_GRADE:
                raise ValueError(f"grade {grade} for {chunk_id} is outside 0-2")
        return value

    @model_validator(mode="after")
    def _has_a_relevant_chunk(self) -> GoldQuery:
        """A query nothing answers measures nothing."""
        if not any(grade >= RELEVANT_FROM for grade in self.judgements.values()):
            raise ValueError(f"{self.query_id} has no chunk graded 1 or above")
        return self


class GoldSet(BaseModel):
    """Every judged query, plus how and when it was built."""

    version: str
    created_at: datetime
    notes: str
    queries: list[GoldQuery]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> GoldSet:
        """Two queries sharing an id would silently overwrite one another."""
        seen = [query.query_id for query in self.queries]
        if len(seen) != len(set(seen)):
            raise ValueError("duplicate query_id in the gold set")
        return self

    def relevant(self, query_id: str) -> set[str]:
        """Every chunk graded 1 or above for this query."""
        for query in self.queries:
            if query.query_id == query_id:
                return {
                    chunk_id
                    for chunk_id, grade in query.judgements.items()
                    if grade >= RELEVANT_FROM
                }
        return set()

    def by_category(self, category: Category) -> list[GoldQuery]:
        """Every query of one shape, for the per-category breakdown."""
        return [query for query in self.queries if query.category is category]


def load_gold_set(path: Path) -> GoldSet:
    """Read and validate the answer key."""
    return GoldSet.model_validate_json(path.read_text(encoding="utf-8"))


def save_gold_set(goldset: GoldSet, path: Path) -> None:
    """Write the answer key, indented so its diffs are reviewable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(goldset.model_dump_json())
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
