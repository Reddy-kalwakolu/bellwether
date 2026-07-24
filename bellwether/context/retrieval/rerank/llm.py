# bellwether/context/retrieval/rerank/llm.py
"""Reranking by asking a model, with the answer shape guaranteed by a schema.

Two decisions worth stating.

Structured output, not parsed prose: the backend is constrained to emit
`[{chunk_id, relevance}]`, so a malformed ranking is impossible rather than merely
unlikely. Retrieval code that regex-scrapes rankings out of an LLM's paragraph is
code that fails silently on the one query that mattered.

A failure degrades to the fused order rather than to nothing. A reranker whose model
is down should hand back the ranking it was given — that is a slightly worse answer.
Handing back an empty list is no answer, and would show up in the eval as a score of
zero, which would be a measurement of the outage rather than of the reranker.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

from bellwether.context.retrieval.rerank.base import RerankerSpec, RerankResult
from bellwether.context.vectors import SearchHit
from bellwether.llm import LLMClient, LLMError

CANDIDATE_DEPTH = 20

# How much of each chunk the model sees. Enough to judge, short enough that twenty
# candidates fit in one call without the bill becoming the story.
SNIPPET_CHARS = 600

# `relevance` is a bare integer, not an enum. Gemini's responseSchema is a
# restricted OpenAPI dialect that rejects integer enum values outright (it wants
# TYPE_STRING), so `enum: [0, 1, 2]` 400s every call and the reranker silently
# degrades to the fused order — invisible to the hermetic tests, which never touch
# the real API. The prompt states the 0/1/2 scale; `_grades` clamps anything else.
RANKING_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "chunk_id": {"type": "string"},
            "relevance": {"type": "integer"},
        },
        "required": ["chunk_id", "relevance"],
    },
}

INSTRUCTIONS = """\
You are ranking retrieved passages from an engineering knowledge base by how well \
each one answers the question.

Grade every candidate:
  2 - fully answers the question on its own
  1 - partially answers it, or is needed context
  0 - does not answer it

Return one entry per candidate. Use the candidate ids exactly as given."""


def build_prompt(query: str, hits: Sequence[SearchHit]) -> str:
    """The ranking prompt: the question, then every candidate with its id."""
    lines = [INSTRUCTIONS, "", f"Question: {query}", "", "Candidates:"]
    for hit in hits:
        anchor = hit.anchor or "(no anchor)"
        snippet = hit.text[:SNIPPET_CHARS].replace("\n", " ")
        lines.append(f"- id: {hit.chunk_id} | {anchor} | {snippet}")
    return "\n".join(lines)


class LLMReranker:
    """Scores the candidate window through an LLM backend, then reorders."""

    def __init__(self, client: LLMClient, candidate_depth: int = CANDIDATE_DEPTH) -> None:
        self.client = client
        self.candidate_depth = candidate_depth

    @property
    def spec(self) -> RerankerSpec:
        """What this reranker is and what it costs."""
        model = self.client.spec
        return RerankerSpec(
            name=f"llm-{model.name}",
            label=f"LLM ({model.label})",
            hosted=model.hosted,
            notes=f"{model.model_id}, top-{self.candidate_depth} window",
        )

    def available(self) -> tuple[bool, str]:
        """Available exactly when its backend is."""
        return self.client.available()

    def rerank(self, query: str, hits: Sequence[SearchHit], limit: int) -> RerankResult:
        """Grade the candidate window, then order by grade and original rank."""
        if not hits:
            return RerankResult(hits=[], usage=None)

        window = list(hits[: self.candidate_depth])
        tail = list(hits[self.candidate_depth :])

        try:
            response = self.client.complete(build_prompt(query, window), RANKING_SCHEMA)
        except LLMError:
            # Degrade to what we were given. See the module docstring.
            return RerankResult(hits=list(hits[:limit]), usage=None)

        grades = _grades(response.data)
        if not grades:
            return RerankResult(hits=list(hits[:limit]), usage=response.usage)

        scored: list[tuple[int, int, SearchHit]] = []
        for position, hit in enumerate(window):
            # An unjudged candidate sits below every judged one but keeps its order.
            scored.append((-grades.get(hit.chunk_id, 0), position, hit))
        scored.sort()

        ordered = [dataclasses.replace(hit, score=float(-grade)) for grade, _, hit in scored]
        return RerankResult(hits=(ordered + tail)[:limit], usage=response.usage)


def _grades(data: object) -> dict[str, int]:
    """`{chunk_id: relevance}` from a well-formed response, else empty."""
    if not isinstance(data, list):
        return {}
    grades: dict[str, int] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        chunk_id = entry.get("chunk_id")
        relevance = entry.get("relevance")
        # `bool` is an `int` subclass — exclude it so a stray `true` is not a grade.
        # Clamp to the 0-2 scale the prompt asks for, now that the schema no longer
        # enforces it (Gemini rejected the enum that used to).
        if (
            isinstance(chunk_id, str)
            and isinstance(relevance, int)
            and not isinstance(relevance, bool)
        ):
            grades[chunk_id] = max(0, min(2, relevance))
    return grades
