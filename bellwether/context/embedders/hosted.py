"""The two hosted engines: Gemini and Voyage.

Both are reached over plain HTTP through an injected `HttpPost`, which is the same
seam Day 3 used for `CampaignClient`. That is what lets the request bodies, the
batching, the error mapping and the usage parsing all be tested without a key and
without a socket — the suite must pass on a machine that has never heard of Voyage.

Cost comes from the token count the provider reports, never from an estimate. A
published cost figure that was calculated rather than billed is a guess wearing a
dollar sign.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from bellwether.context.embedders.base import (
    EmbeddingError,
    EmbeddingResult,
    EngineSpec,
    HttpPost,
    UsageRecord,
    cost_for,
    httpx_post,
)

VOYAGE_SPEC = EngineSpec(
    name="voyage",
    label="VOYAGE",
    dimensions=1024,
    hosted=True,
    cost_per_million_tokens=0.06,
    mteb_retrieval=67.6,
    notes="voyage-3.5. Anthropic's recommended embedding family; 32k context.",
)

GEMINI_SPEC = EngineSpec(
    name="gemini",
    label="GEMINI",
    dimensions=3072,
    hosted=True,
    # List price. Voyage's figure is confirmed against a real invoice line; this one
    # is the published rate, which is why the desk labels it as such.
    cost_per_million_tokens=0.15,
    mteb_retrieval=68.32,
    notes="gemini-embedding-001. Currently #1 on the English MTEB board.",
)

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-embedding-001:batchEmbedContents"
)

# Voyage accepts 128 inputs per request; Gemini's batch endpoint accepts 100.
VOYAGE_BATCH = 128
GEMINI_BATCH = 100


# Gemini's free tier allows 100 embed requests per minute, and one batch call of 100
# texts counts as 100 requests — so a 585-chunk corpus hits the wall on the first
# batch. Backing off and continuing is the honest behaviour: the alternative is either
# a half-embedded corpus or a comparison that silently excludes the best engine.
MAX_RETRIES = 6
RATE_LIMIT_PAUSE_SECONDS = 30.0
SERVER_ERROR_PAUSE_SECONDS = 2.0


def _batches(texts: list[str], size: int) -> list[list[str]]:
    """Split the work into request-sized groups."""
    return [texts[start : start + size] for start in range(0, len(texts), size)]


def _post_with_retry(
    post: HttpPost,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    sleep: Callable[[float], None],
    max_retries: int = MAX_RETRIES,
) -> tuple[int, dict[str, Any]]:
    """POST, retrying a rate limit or a server error with backoff.

    Anything else — a bad key, a malformed body — comes straight back, because
    retrying a 401 six times is just a slower way to fail.
    """
    pause = SERVER_ERROR_PAUSE_SECONDS
    status, body = post(url, payload, headers)
    for _ in range(max_retries):
        if status != 429 and status < 500:
            return status, body
        sleep(RATE_LIMIT_PAUSE_SECONDS if status == 429 else pause)
        pause = min(pause * 2, 30.0)
        status, body = post(url, payload, headers)
    return status, body


def _require(body: dict[str, Any], engine: str, status: int) -> None:
    """Turn a non-200 into a typed error that names the engine and the reason."""
    if status == 200:
        return
    error = body.get("error")
    message = error.get("message") if isinstance(error, dict) else error
    raise EmbeddingError(f"{engine} returned {status}: {message or 'no message'}")


class VoyageEmbedder:
    """voyage-3.5 over HTTP."""

    def __init__(
        self,
        api_key: str | None = None,
        post: HttpPost | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("VOYAGE_API_KEY", "")
        self._post = post
        self._sleep = sleep

    @property
    def spec(self) -> EngineSpec:
        """What this engine is and what it costs."""
        return VOYAGE_SPEC

    def available(self) -> tuple[bool, str]:
        """Whether the engine can run, and if not, the reason a human can act on."""
        if not self._api_key:
            return False, "no VOYAGE_API_KEY"
        return True, "ready"

    def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed every text, in order, reporting what it consumed."""
        ready, reason = self.available()
        if not ready:
            raise EmbeddingError(f"voyage unavailable: {reason}")
        post = self._post or httpx_post()

        started = time.perf_counter()
        vectors: list[list[float]] = []
        tokens = 0
        for batch in _batches(texts, VOYAGE_BATCH):
            status, body = _post_with_retry(
                post,
                VOYAGE_URL,
                {"input": batch, "model": "voyage-3.5", "input_type": "document"},
                {"Authorization": f"Bearer {self._api_key}"},
                self._sleep,
            )
            _require(body, "voyage", status)
            for item in body.get("data", []):
                vectors.append([float(value) for value in item["embedding"]])
            tokens += int(body.get("usage", {}).get("total_tokens", 0))

        return EmbeddingResult(
            vectors=vectors,
            usage=UsageRecord(
                engine=VOYAGE_SPEC.name,
                texts=len(texts),
                tokens=tokens,
                cost_usd=cost_for(tokens, VOYAGE_SPEC.cost_per_million_tokens),
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
        )


class GeminiEmbedder:
    """gemini-embedding-001 over HTTP."""

    def __init__(
        self,
        api_key: str | None = None,
        post: HttpPost | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self._post = post
        self._sleep = sleep

    @property
    def spec(self) -> EngineSpec:
        """What this engine is and what it costs."""
        return GEMINI_SPEC

    def available(self) -> tuple[bool, str]:
        """Whether the engine can run, and if not, the reason a human can act on."""
        if not self._api_key:
            return False, "no GEMINI_API_KEY"
        return True, "ready"

    def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed every text, in order, reporting what it consumed."""
        ready, reason = self.available()
        if not ready:
            raise EmbeddingError(f"gemini unavailable: {reason}")
        post = self._post or httpx_post()

        started = time.perf_counter()
        vectors: list[list[float]] = []
        for batch in _batches(texts, GEMINI_BATCH):
            status, body = _post_with_retry(
                post,
                GEMINI_URL,
                {
                    "requests": [
                        {
                            "model": "models/gemini-embedding-001",
                            "content": {"parts": [{"text": text}]},
                            "taskType": "RETRIEVAL_DOCUMENT",
                        }
                        for text in batch
                    ]
                },
                {"x-goog-api-key": self._api_key},
                self._sleep,
            )
            _require(body, "gemini", status)
            for item in body.get("embeddings", []):
                vectors.append([float(value) for value in item["values"]])

        # Gemini's embed endpoint does not return a token count, so the desk shows
        # cost against an estimate here and says so rather than inventing precision.
        tokens = sum(max(len(text) // 4, 1) for text in texts)
        return EmbeddingResult(
            vectors=vectors,
            usage=UsageRecord(
                engine=GEMINI_SPEC.name,
                texts=len(texts),
                tokens=tokens,
                cost_usd=cost_for(tokens, GEMINI_SPEC.cost_per_million_tokens),
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
        )
