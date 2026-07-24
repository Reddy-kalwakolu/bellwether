# bellwether/llm/gemini.py
"""Gemini, over plain HTTP with an injected transport.

Wired first because its key already works and is billed — Day 7's embedding run
spent $0.0223 through it — so Day 8 can produce a real reranking number without
waiting on a new billing relationship.
"""

from __future__ import annotations

import os
import time
from typing import Any

from bellwether.context.embedders.base import HttpPost, UsageRecord, cost_for
from bellwether.llm.base import (
    DEFAULT_MAX_TOKENS,
    LLMError,
    LLMResponse,
    ModelSpec,
    env_model,
    httpx_post,
    parse_structured,
)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

# The `-latest` alias, not a pinned snapshot. `gemini-2.5-flash` 404s for new users
# ("no longer available") — the exact stale-model trap §4.6 warns against, and it
# degraded the reranker silently because a failed call falls back to the fused order.
# The alias always resolves to the current flash model. Override with
# BELLWETHER_GEMINI_MODEL to pin a snapshot.
DEFAULT_MODEL = "gemini-flash-latest"


class GeminiClient:
    """One completion call, a schema, and a cost read from the response."""

    def __init__(
        self,
        api_key: str | None = None,
        transport: HttpPost | None = None,
        model_id: str | None = None,
    ) -> None:
        self._api_key = os.environ.get("GEMINI_API_KEY", "") if api_key is None else api_key
        self._transport = transport
        self._model_id = model_id or env_model("BELLWETHER_GEMINI_MODEL", DEFAULT_MODEL)

    @property
    def spec(self) -> ModelSpec:
        """What this backend is and what it charges."""
        return ModelSpec(
            name="gemini",
            label="Gemini",
            model_id=self._model_id,
            hosted=True,
            cost_per_million_input=0.30,
            cost_per_million_output=2.50,
            notes="wired first — the key already works and is billed",
        )

    def available(self) -> tuple[bool, str]:
        """Whether the key is present, and which one is missing if not."""
        if not self._api_key:
            return False, "no GEMINI_API_KEY"
        return True, f"ready ({self._model_id})"

    def complete(
        self, prompt: str, schema: dict[str, Any], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> LLMResponse:
        """Answer `prompt` as JSON matching `schema`, and report the bill."""
        available, reason = self.available()
        if not available:
            raise LLMError(f"gemini unavailable: {reason}")

        post = self._transport or httpx_post()
        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "maxOutputTokens": max_tokens,
            },
        }
        # The key rides in the header, never the body — the body is what gets logged.
        headers = {"x-goog-api-key": self._api_key, "content-type": "application/json"}
        url = f"{ENDPOINT}/{self._model_id}:generateContent"

        started = time.perf_counter()
        status, body = post(url, payload, headers)
        elapsed_ms = (time.perf_counter() - started) * 1000

        if status != 200:
            # `body.get("error", {})` only defaults when the key is absent. A present
            # `"error": null` or `"error": "gateway timeout"` — both routine from
            # proxies on a 429 or 5xx — would then hit .get() on None or a str and
            # raise AttributeError, which is the one thing this branch exists to
            # prevent. Day 7's embedders/hosted.py `_require` already guards this way.
            error = body.get("error")
            message = error.get("message", error) if isinstance(error, dict) else (error or body)
            raise LLMError(f"gemini returned {status}: {message}")

        text = _first_text(body)
        usage = body.get("usageMetadata", {})
        input_tokens = int(usage.get("promptTokenCount") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or 0)
        spec = self.spec

        return LLMResponse(
            text=text,
            data=parse_structured("gemini", text),
            usage=UsageRecord(
                engine="gemini",
                texts=1,
                tokens=input_tokens + output_tokens,
                cost_usd=cost_for(input_tokens, spec.cost_per_million_input)
                + cost_for(output_tokens, spec.cost_per_million_output),
                latency_ms=elapsed_ms,
            ),
        )


def _first_text(body: dict[str, Any]) -> str:
    """The first text part of the first candidate, or a typed failure."""
    candidates = body.get("candidates") or []
    if not candidates:
        raise LLMError(f"gemini returned no candidates: {body}")
    parts = candidates[0].get("content", {}).get("parts") or []
    if not parts:
        raise LLMError(f"gemini returned no parts: {body}")
    return str(parts[0].get("text", ""))
