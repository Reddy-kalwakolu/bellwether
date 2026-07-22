"""Four engines, one protocol — and not a single socket opened."""

from __future__ import annotations

import math
from typing import Any

import pytest

from bellwether.context.embedders import (
    DEFAULT_ENGINE,
    REGISTRY,
    EmbeddingError,
    GeminiEmbedder,
    HashingEmbedder,
    Model2VecEmbedder,
    VoyageEmbedder,
    engine_specs,
    get_embedder,
)
from bellwether.context.embedders.base import cost_for
from bellwether.context.embedders.hashing import DIMENSIONS


class FakePost:
    """Records what an engine would have sent, and answers with a canned body."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {}
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, payload, headers))
        return self.status, self.body


def _voyage_body(count: int, tokens: int = 40) -> dict[str, Any]:
    return {
        "data": [{"embedding": [0.1] * 1024} for _ in range(count)],
        "usage": {"total_tokens": tokens},
    }


def _gemini_body(count: int) -> dict[str, Any]:
    return {"embeddings": [{"values": [0.2] * 3072} for _ in range(count)]}


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


# --- the registry --------------------------------------------------------------


def test_every_engine_is_registered_and_specced() -> None:
    assert set(REGISTRY) == {"gemini", "voyage", "potion", "hashing"}
    for spec in engine_specs():
        assert spec.label
        assert spec.dimensions > 0
        assert spec.notes


def test_the_default_engine_needs_no_key_and_no_download() -> None:
    ready, _ = get_embedder(DEFAULT_ENGINE).available()
    assert ready is True


def test_an_unregistered_engine_is_a_key_error() -> None:
    with pytest.raises(KeyError):
        get_embedder("word2vec")


# --- hashing -------------------------------------------------------------------


def test_the_hashing_engine_is_deterministic() -> None:
    first = HashingEmbedder().embed(["frequency capping"])
    second = HashingEmbedder().embed(["frequency capping"])
    assert first.vectors == second.vectors


def test_hashing_vectors_are_unit_length() -> None:
    [vector] = HashingEmbedder().embed(["budget pacing and brand safety"]).vectors
    assert math.isclose(_cosine(vector, vector), 1.0, abs_tol=1e-9)
    assert len(vector) == DIMENSIONS


def test_shared_vocabulary_scores_higher_than_unrelated_text() -> None:
    result = HashingEmbedder().embed(
        [
            "campaign budget pacing throttled the impression",
            "campaign budget pacing stopped throttling",
            "the grafana dashboard renders a state timeline panel",
        ]
    )
    related = _cosine(result.vectors[0], result.vectors[1])
    unrelated = _cosine(result.vectors[0], result.vectors[2])
    # Not a stub: this is a real lexical signal, which is what lets CI assert
    # retrieval behaviour with no model present.
    assert related > unrelated


def test_the_hashing_engine_is_free_and_counts_its_tokens() -> None:
    usage = HashingEmbedder().embed(["one two three"]).usage
    assert usage.cost_usd == 0.0
    assert usage.tokens == 3
    assert usage.texts == 1
    assert usage.latency_ms >= 0


def test_an_empty_string_does_not_divide_by_zero() -> None:
    [vector] = HashingEmbedder().embed([""]).vectors
    assert all(component == 0.0 for component in vector)


# --- voyage --------------------------------------------------------------------


def test_voyage_is_unavailable_without_a_key_and_says_why() -> None:
    ready, reason = VoyageEmbedder(api_key="").available()
    assert ready is False
    assert "VOYAGE_API_KEY" in reason


def test_embedding_without_a_key_raises_rather_than_calling_out() -> None:
    with pytest.raises(EmbeddingError, match="voyage unavailable"):
        VoyageEmbedder(api_key="").embed(["text"])


def test_voyage_sends_the_model_and_authorises_the_request() -> None:
    post = FakePost(body=_voyage_body(1))
    VoyageEmbedder(api_key="secret", post=post).embed(["a chunk"])

    url, payload, headers = post.calls[0]
    assert url.endswith("/v1/embeddings")
    assert payload["model"] == "voyage-3.5"
    assert payload["input"] == ["a chunk"]
    assert headers["Authorization"] == "Bearer secret"


def test_voyage_bills_from_the_tokens_the_provider_reported() -> None:
    post = FakePost(body=_voyage_body(1, tokens=1_000_000))
    usage = VoyageEmbedder(api_key="k", post=post).embed(["x"]).usage
    # A published cost that was estimated rather than billed is a guess in a dollar sign.
    assert usage.tokens == 1_000_000
    assert math.isclose(usage.cost_usd, 0.06)


def test_voyage_splits_large_corpora_into_batches() -> None:
    post = FakePost(body=_voyage_body(128))
    VoyageEmbedder(api_key="k", post=post).embed(["chunk"] * 300)
    assert len(post.calls) == 3
    assert [len(call[1]["input"]) for call in post.calls] == [128, 128, 44]


def test_a_voyage_error_names_the_engine_and_the_reason() -> None:
    post = FakePost(status=401, body={"error": {"message": "invalid key"}})
    with pytest.raises(EmbeddingError, match="voyage returned 401.*invalid key"):
        VoyageEmbedder(api_key="k", post=post).embed(["x"])


# --- gemini --------------------------------------------------------------------


def test_gemini_is_unavailable_without_a_key_and_says_why() -> None:
    ready, reason = GeminiEmbedder(api_key="").available()
    assert ready is False
    assert "GEMINI_API_KEY" in reason


def test_gemini_sends_a_batch_request_with_the_retrieval_task_type() -> None:
    post = FakePost(body=_gemini_body(2))
    GeminiEmbedder(api_key="secret", post=post).embed(["one", "two"])

    url, payload, headers = post.calls[0]
    assert url.endswith(":batchEmbedContents")
    assert headers["x-goog-api-key"] == "secret"
    assert len(payload["requests"]) == 2
    assert payload["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"
    assert payload["requests"][0]["content"]["parts"][0]["text"] == "one"


def test_gemini_returns_three_thousand_dimensional_vectors() -> None:
    post = FakePost(body=_gemini_body(1))
    [vector] = GeminiEmbedder(api_key="k", post=post).embed(["x"]).vectors
    assert len(vector) == 3072


def test_gemini_batches_at_one_hundred() -> None:
    post = FakePost(body=_gemini_body(100))
    GeminiEmbedder(api_key="k", post=post).embed(["chunk"] * 250)
    assert [len(call[1]["requests"]) for call in post.calls] == [100, 100, 50]


def test_a_gemini_error_names_the_engine() -> None:
    post = FakePost(status=429, body={"error": {"message": "quota"}})
    with pytest.raises(EmbeddingError, match="gemini returned 429"):
        GeminiEmbedder(api_key="k", post=post).embed(["x"])


# --- potion --------------------------------------------------------------------


def test_potion_reports_an_actionable_reason_when_it_is_not_installed() -> None:
    ready, reason = Model2VecEmbedder().available()
    if not ready:
        assert "uv sync --group embeddings" in reason
    else:
        assert reason == "ready"


def test_potion_refuses_rather_than_raising_an_import_error() -> None:
    embedder = Model2VecEmbedder()
    ready, _ = embedder.available()
    if ready:
        pytest.skip("model2vec is installed; the unavailable path cannot be exercised")
    with pytest.raises(EmbeddingError, match="potion unavailable"):
        embedder.embed(["x"])


# --- cost ----------------------------------------------------------------------


def test_cost_is_linear_in_tokens() -> None:
    assert cost_for(1_000_000, 0.06) == 0.06
    assert cost_for(0, 0.15) == 0.0
    assert math.isclose(cost_for(140_000, 0.06), 0.0084)
