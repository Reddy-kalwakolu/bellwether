# tests/bellwether/llm/test_llm.py
"""One protocol, two backends, and no test that opens a socket."""

from __future__ import annotations

import json
from typing import Any

import pytest

from bellwether.llm import REGISTRY, ClaudeClient, GeminiClient, LLMError, get_client

SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"chunk_id": {"type": "string"}, "relevance": {"type": "integer"}},
        "required": ["chunk_id", "relevance"],
    },
}


class FakePost:
    """Answers one scripted response, and records what it was sent."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, payload, headers))
        return self.status, self.body


def _gemini_body(text: str, prompt_tokens: int = 100, output_tokens: int = 20) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": output_tokens,
        },
    }


def _claude_body(text: str, input_tokens: int = 100, output_tokens: int = 20) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def test_gemini_is_unavailable_without_a_key_and_says_which_one() -> None:
    available, reason = GeminiClient(api_key="").available()
    assert available is False
    assert "GEMINI_API_KEY" in reason


def test_claude_is_unavailable_without_a_key_and_says_which_one() -> None:
    available, reason = ClaudeClient(api_key="").available()
    assert available is False
    assert "ANTHROPIC_API_KEY" in reason


def test_gemini_parses_structured_output() -> None:
    payload = json.dumps([{"chunk_id": "a#0001", "relevance": 2}])
    client = GeminiClient(api_key="k", transport=FakePost(200, _gemini_body(payload)))
    response = client.complete("rank these", SCHEMA)
    assert response.data == [{"chunk_id": "a#0001", "relevance": 2}]


def test_gemini_sends_the_schema_and_asks_for_json() -> None:
    post = FakePost(200, _gemini_body("[]"))
    GeminiClient(api_key="k", transport=post).complete("rank these", SCHEMA)
    _, payload, _ = post.calls[0]
    config = payload["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == SCHEMA


def test_gemini_never_puts_the_key_in_the_payload() -> None:
    post = FakePost(200, _gemini_body("[]"))
    GeminiClient(api_key="sekrit", transport=post).complete("rank these", SCHEMA)
    _, payload, _ = post.calls[0]
    assert "sekrit" not in json.dumps(payload)


def test_gemini_computes_cost_from_the_reported_tokens() -> None:
    post = FakePost(200, _gemini_body("[]", prompt_tokens=1_000_000, output_tokens=0))
    client = GeminiClient(api_key="k", transport=post)
    response = client.complete("rank these", SCHEMA)
    assert response.usage.tokens == 1_000_000
    assert response.usage.cost_usd == pytest.approx(client.spec.cost_per_million_input)


def test_gemini_maps_a_non_200_to_a_typed_error_naming_the_backend() -> None:
    client = GeminiClient(api_key="k", transport=FakePost(429, {"error": {"message": "slow down"}}))
    with pytest.raises(LLMError, match="gemini"):
        client.complete("rank these", SCHEMA)


def test_malformed_json_raises_rather_than_returning_junk() -> None:
    client = GeminiClient(api_key="k", transport=FakePost(200, _gemini_body("not json at all")))
    with pytest.raises(LLMError, match="gemini"):
        client.complete("rank these", SCHEMA)


def test_claude_parses_structured_output() -> None:
    payload = json.dumps([{"chunk_id": "a#0001", "relevance": 1}])
    client = ClaudeClient(api_key="k", transport=FakePost(200, _claude_body(payload)))
    response = client.complete("rank these", SCHEMA)
    assert response.data == [{"chunk_id": "a#0001", "relevance": 1}]


def test_claude_sends_the_key_as_a_header_and_never_in_the_body() -> None:
    post = FakePost(200, _claude_body("[]"))
    ClaudeClient(api_key="sekrit", transport=post).complete("rank these", SCHEMA)
    _, payload, headers = post.calls[0]
    assert headers["x-api-key"] == "sekrit"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "sekrit" not in json.dumps(payload)


def test_claude_does_not_send_effort_which_haiku_rejects() -> None:
    post = FakePost(200, _claude_body("[]"))
    ClaudeClient(api_key="k", transport=post).complete("rank these", SCHEMA)
    _, payload, _ = post.calls[0]
    assert "output_config" not in payload


def test_claude_bills_input_and_output_at_different_rates() -> None:
    post = FakePost(200, _claude_body("[]", input_tokens=1_000_000, output_tokens=1_000_000))
    client = ClaudeClient(api_key="k", transport=post)
    response = client.complete("rank these", SCHEMA)
    expected = client.spec.cost_per_million_input + client.spec.cost_per_million_output
    assert response.usage.cost_usd == pytest.approx(expected)


def test_the_registry_lists_both_backends() -> None:
    assert set(REGISTRY) == {"gemini", "claude"}


def test_get_client_rejects_an_unknown_name() -> None:
    with pytest.raises(KeyError):
        get_client("gpt")
