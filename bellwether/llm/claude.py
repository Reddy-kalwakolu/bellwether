# bellwether/llm/claude.py
"""Claude — written, hermetically tested, and never yet run against the live API.

Spec section 4.6 names Claude API (Haiku-class for dev work) as the platform's LLM,
and every agent from Level 2 onward assumes it. Shipping only Gemini would leave the
whole AI layer on a provider Level 2 does not use, so the backend is implemented
here rather than stubbed: when the key arrives, it works.

What is owed is the credential, not the code. Until ANTHROPIC_API_KEY exists this
class has never made a real request, and the devlog says so rather than implying a
verification that did not happen.

`claude-haiku-4-5` is a pre-4.6 model: `output_config.effort` errors on it, so it is
not sent. Structured output is requested through a tool with an input schema, which
is the shape this model supports.
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

ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5"
TOOL_NAME = "emit_ranking"


class ClaudeClient:
    """One messages call, structured output through a tool, and a per-call bill."""

    def __init__(
        self,
        api_key: str | None = None,
        transport: HttpPost | None = None,
        model_id: str | None = None,
    ) -> None:
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "") if api_key is None else api_key
        self._transport = transport
        self._model_id = model_id or env_model("BELLWETHER_CLAUDE_MODEL", DEFAULT_MODEL)

    @property
    def spec(self) -> ModelSpec:
        """What this backend is and what it charges."""
        return ModelSpec(
            name="claude",
            label="Claude Haiku",
            model_id=self._model_id,
            hosted=True,
            cost_per_million_input=1.00,
            cost_per_million_output=5.00,
            notes="owed since Day 8 — code written, never run against the live API",
        )

    def available(self) -> tuple[bool, str]:
        """Whether the key is present, and which one is missing if not."""
        if not self._api_key:
            return False, "no ANTHROPIC_API_KEY"
        return True, f"ready ({self._model_id})"

    def complete(
        self, prompt: str, schema: dict[str, Any], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> LLMResponse:
        """Answer `prompt` as JSON matching `schema`, and report the bill."""
        available, reason = self.available()
        if not available:
            raise LLMError(f"claude unavailable: {reason}")

        post = self._transport or httpx_post()
        payload: dict[str, Any] = {
            "model": self._model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": "Emit the ranking as structured data.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"ranking": schema},
                        "required": ["ranking"],
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

        started = time.perf_counter()
        status, body = post(ENDPOINT, payload, headers)
        elapsed_ms = (time.perf_counter() - started) * 1000

        if status != 200:
            # `body.get("error", {})` only defaults when the key is absent. A present
            # `"error": null` or `"error": "gateway timeout"` — both routine from
            # proxies on a 429 or 5xx — would then hit .get() on None or a str and
            # raise AttributeError, which is the one thing this branch exists to
            # prevent. Day 7's embedders/hosted.py `_require` already guards this way.
            error = body.get("error")
            message = error.get("message", error) if isinstance(error, dict) else (error or body)
            raise LLMError(f"claude returned {status}: {message}")

        text, data = _extract(body)
        usage = body.get("usage", {})
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        spec = self.spec

        return LLMResponse(
            text=text,
            data=data,
            usage=UsageRecord(
                engine="claude",
                texts=1,
                tokens=input_tokens + output_tokens,
                cost_usd=cost_for(input_tokens, spec.cost_per_million_input)
                + cost_for(output_tokens, spec.cost_per_million_output),
                latency_ms=elapsed_ms,
            ),
        )


def _extract(body: dict[str, Any]) -> tuple[str, object]:
    """The tool input if the model called the tool, else the text block."""
    blocks = body.get("content") or []
    if not blocks:
        raise LLMError(f"claude returned no content: {body}")

    for block in blocks:
        # Guarded for the same reason the non-200 branch above is: a content list
        # holding anything but dicts would otherwise raise AttributeError rather than
        # the typed LLMError this module promises.
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            payload = block.get("input", {})
            return str(payload), payload.get("ranking", payload)

    text = str(blocks[0].get("text", ""))
    return text, parse_structured("claude", text)
