"""One protocol, four engines, and a cost recorded for every call.

The engine is a parameter, not a commitment. Everything here exists so the same chunks
can be embedded by a hosted frontier model, a local static model, and a dependency-free
baseline, and compared honestly — a comparison where each engine gets its own chunks or
its own store is not a comparison, it is four anecdotes.

Cost tracking lives at this layer rather than in each engine because it is an
accountability feature, not an implementation detail: the spec promises per-call token
and cost tracking across the whole platform, and embeddings are where it starts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


class EmbeddingError(RuntimeError):
    """An engine could not embed. Always names the engine that failed."""


@dataclass(frozen=True)
class EngineSpec:
    """What an engine is, and what it costs — the row it occupies on the desk."""

    name: str
    label: str
    dimensions: int
    hosted: bool
    cost_per_million_tokens: float
    mteb_retrieval: float | None
    notes: str


@dataclass(frozen=True)
class UsageRecord:
    """What one embed call consumed."""

    engine: str
    texts: int
    tokens: int
    cost_usd: float
    latency_ms: float


@dataclass(frozen=True)
class EmbeddingResult:
    """Vectors, and the bill for producing them."""

    vectors: list[list[float]]
    usage: UsageRecord


class Embedder(Protocol):
    """Everything the pipeline and the desk need from an embedding engine."""

    @property
    def spec(self) -> EngineSpec:
        """What this engine is and what it costs."""
        ...

    def available(self) -> tuple[bool, str]:
        """Whether the engine can run, and if not, the reason a human can act on."""
        ...

    def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed every text, in order, reporting what it consumed."""
        ...


class HttpPost(Protocol):
    """The seam hosted engines are tested through. No test opens a socket."""

    def __call__(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        """POST JSON, returning the status code and the decoded body."""
        ...


def cost_for(tokens: int, cost_per_million_tokens: float) -> float:
    """What `tokens` cost at this engine's rate."""
    return tokens * cost_per_million_tokens / 1_000_000


def httpx_post(timeout_seconds: float = 60.0) -> HttpPost:
    """The real transport. Imported lazily so tests never construct a client."""
    import httpx

    def post(
        url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout_seconds)
        try:
            body: dict[str, Any] = response.json()
        except json.JSONDecodeError:
            body = {"error": {"message": response.text[:500]}}
        return response.status_code, body

    return post
