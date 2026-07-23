# bellwether/llm/base.py
"""The provider seam. Deliberately thin — a protocol, a transport, and a bill.

Spec section 4.6 calls for an LLM abstraction with per-call token and cost tracking
and no model names hardcoded in logic. This is that, and no more: not a framework,
not a chain, not an agent runtime. Those arrive at Level 4 and will sit on top of
this rather than replacing it.

Cost tracking reuses Day 7's `UsageRecord` verbatim rather than defining a parallel
vocabulary, so one report can total embedding spend and generation spend together.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from bellwether.context.embedders.base import HttpPost, UsageRecord

DEFAULT_MAX_TOKENS = 2048


class LLMError(RuntimeError):
    """A backend could not complete. Always names the backend that failed."""


@dataclass(frozen=True)
class ModelSpec:
    """What a backend is and what it charges, input and output priced apart."""

    name: str
    label: str
    model_id: str
    hosted: bool
    cost_per_million_input: float
    cost_per_million_output: float
    notes: str


@dataclass(frozen=True)
class LLMResponse:
    """The text, the parsed structure if a schema was given, and the bill."""

    text: str
    data: object | None
    usage: UsageRecord


class LLMClient(Protocol):
    """Everything the rerankers and, later, the agents need from a provider."""

    @property
    def spec(self) -> ModelSpec:
        """What this backend is and what it costs."""
        ...

    def available(self) -> tuple[bool, str]:
        """Whether it can run, and if not, a reason a human can act on."""
        ...

    def complete(
        self, prompt: str, schema: dict[str, Any], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> LLMResponse:
        """Answer `prompt`, constrained to `schema`, reporting what it consumed."""
        ...


def env_model(variable: str, fallback: str) -> str:
    """A model id from the environment, or the published default.

    Never hardcode a model id in logic. Two-year-stale model names in a portfolio
    repo signal copy-paste planning, and providers rename faster than a 30-day build
    can keep up with.
    """
    return os.environ.get(variable) or fallback


def parse_structured(backend: str, text: str) -> object:
    """Decode a structured response, or fail loudly naming the backend."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise LLMError(f"{backend} returned text that is not JSON: {text[:200]}") from error


def httpx_post(timeout_seconds: float = 60.0) -> HttpPost:
    """The real transport. Imported lazily so tests never construct a client."""
    import httpx

    def post(
        url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout_seconds)
        try:
            body: dict[str, Any] = response.json()
        except ValueError:
            body = {"error": {"message": response.text[:500]}}
        return response.status_code, body

    return post
