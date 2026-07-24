# tests/bellwether/context/retrieval/rerank/test_llm_reranker.py
"""Reranking through a model, with a fake backend and no socket in sight."""

from __future__ import annotations

from typing import Any

from bellwether.context.embedders.base import UsageRecord
from bellwether.context.retrieval.rerank import RANKING_SCHEMA, LLMReranker
from bellwether.context.retrieval.rerank.llm import build_prompt
from bellwether.context.vectors import SearchHit
from bellwether.llm import LLMError
from bellwether.llm.base import LLMResponse, ModelSpec


def test_the_relevance_field_carries_no_enum_gemini_rejects() -> None:
    # Gemini's responseSchema is a restricted OpenAPI dialect that 400s on integer
    # enums. An enum here would degrade every live Gemini rerank to the fused order,
    # invisibly to this hermetic suite. Keep it a bare integer.
    relevance = RANKING_SCHEMA["items"]["properties"]["relevance"]
    assert relevance == {"type": "integer"}
    assert "enum" not in relevance


def test_a_grade_outside_the_scale_is_clamped_not_trusted() -> None:
    # The schema no longer constrains the range, so the parser must. A model that
    # returns 7 or -3 should not out- or under-rank a legitimate 2.
    client = FakeClient(
        data=[
            {"chunk_id": "a", "relevance": 7},
            {"chunk_id": "b", "relevance": 2},
            {"chunk_id": "c", "relevance": -3},
        ]
    )
    hits = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
    result = LLMReranker(client).rerank("q", hits, limit=3)
    assert result.hits[0].score == 2.0
    assert result.hits[-1].score == 0.0


def test_a_boolean_relevance_is_not_treated_as_a_grade() -> None:
    # bool is an int subclass; a stray `true` must not become grade 1.
    client = FakeClient(data=[{"chunk_id": "a", "relevance": True}])
    result = LLMReranker(client).rerank("q", [_hit("a", 0.9)], limit=1)
    assert [h.chunk_id for h in result.hits] == ["a"]


def _hit(chunk_id: str, score: float, text: str = "body text") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=score,
        text=text,
        anchor=chunk_id,
        source_path=f"docs/{chunk_id}.md",
        source_type="adr",
        component="docs",
        title=chunk_id,
    )


class FakeClient:
    """Returns a scripted ranking, or raises, and records the prompt it saw."""

    def __init__(self, data: object | None = None, error: Exception | None = None) -> None:
        self.data = data
        self.error = error
        self.prompts: list[str] = []

    @property
    def spec(self) -> ModelSpec:
        return ModelSpec(
            name="fake",
            label="Fake",
            model_id="fake-1",
            hosted=False,
            cost_per_million_input=0.0,
            cost_per_million_output=0.0,
            notes="test double",
        )

    def available(self) -> tuple[bool, str]:
        return True, "ready"

    def complete(self, prompt: str, schema: dict[str, Any], max_tokens: int = 2048) -> LLMResponse:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return LLMResponse(
            text="",
            data=self.data,
            usage=UsageRecord(engine="fake", texts=1, tokens=120, cost_usd=0.0, latency_ms=5.0),
        )


def test_reorders_by_the_relevance_the_model_returned() -> None:
    client = FakeClient(
        data=[
            {"chunk_id": "b", "relevance": 2},
            {"chunk_id": "a", "relevance": 0},
        ]
    )
    hits = [_hit("a", 0.9), _hit("b", 0.4)]
    result = LLMReranker(client).rerank("anything", hits, limit=2)
    assert [hit.chunk_id for hit in result.hits] == ["b", "a"]


def test_reports_the_usage_the_backend_recorded() -> None:
    client = FakeClient(data=[{"chunk_id": "a", "relevance": 1}])
    result = LLMReranker(client).rerank("anything", [_hit("a", 0.9)], limit=1)
    assert result.usage is not None
    assert result.usage.tokens == 120


def test_a_backend_failure_degrades_to_the_fused_order() -> None:
    client = FakeClient(error=LLMError("fake exploded"))
    hits = [_hit("a", 0.9), _hit("b", 0.4)]
    result = LLMReranker(client).rerank("anything", hits, limit=2)
    assert [hit.chunk_id for hit in result.hits] == ["a", "b"]
    assert result.usage is None


def test_chunks_the_model_omitted_keep_their_place_behind_the_ranked_ones() -> None:
    client = FakeClient(data=[{"chunk_id": "c", "relevance": 2}])
    hits = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.1)]
    result = LLMReranker(client).rerank("anything", hits, limit=3)
    assert result.hits[0].chunk_id == "c"
    assert {hit.chunk_id for hit in result.hits} == {"a", "b", "c"}


def test_an_id_the_model_invented_is_ignored() -> None:
    client = FakeClient(data=[{"chunk_id": "does-not-exist", "relevance": 2}])
    result = LLMReranker(client).rerank("anything", [_hit("a", 0.9)], limit=1)
    assert [hit.chunk_id for hit in result.hits] == ["a"]


def test_a_non_list_response_degrades_rather_than_raising() -> None:
    client = FakeClient(data={"unexpected": "shape"})
    result = LLMReranker(client).rerank("anything", [_hit("a", 0.9)], limit=1)
    assert [hit.chunk_id for hit in result.hits] == ["a"]


def test_only_the_candidate_window_is_sent() -> None:
    client = FakeClient(data=[])
    hits = [_hit(f"c{index}", 1.0 / (index + 1)) for index in range(30)]
    LLMReranker(client, candidate_depth=5).rerank("anything", hits, limit=5)
    prompt = client.prompts[0]
    assert "c0" in prompt
    assert "c9" not in prompt


def test_empty_input_reranks_to_empty_without_calling_the_backend() -> None:
    client = FakeClient(data=[])
    result = LLMReranker(client).rerank("anything", [], limit=5)
    assert result.hits == []
    assert client.prompts == []


def test_the_prompt_names_every_candidate_and_the_query() -> None:
    prompt = build_prompt("why qdrant", [_hit("a", 0.9), _hit("b", 0.8)])
    assert "why qdrant" in prompt
    assert "a" in prompt
    assert "b" in prompt
