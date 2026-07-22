"""The free tier: potion, a static embedding model that needs no GPU and no torch.

Model2Vec precomputes the token embeddings a sentence-transformer would produce, so
inference is a lookup and a mean rather than a forward pass. That collapses the
install from ~2.5 GB of PyTorch to numpy and a tokenizer, and the runtime from a
model load to a dictionary read — at roughly 82% of all-MiniLM-L6-v2's retrieval
score (ADR-0007).

It is optional on purpose. The import is lazy and a missing package is a disabled
engine with an actionable reason, never an ImportError at startup.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Protocol

from bellwether.context.embedders.base import (
    EmbeddingError,
    EmbeddingResult,
    EngineSpec,
    UsageRecord,
)

MODEL_NAME = "minishlab/potion-retrieval-32M"

SPEC = EngineSpec(
    name="potion",
    label="POTION",
    dimensions=512,
    hosted=False,
    cost_per_million_tokens=0.0,
    mteb_retrieval=35.06,
    notes=f"{MODEL_NAME}. Static embeddings — numpy only, no torch, no GPU, no network.",
)

INSTALL_HINT = "not installed — python -m uv sync --group embeddings"


class StaticModelLike(Protocol):
    """The one method this module needs from model2vec.

    Declared structurally so the module type-checks on a machine where the optional
    package is absent — which is every CI runner.
    """

    def encode(self, sentences: list[str]) -> Iterable[Iterable[float]]:
        """Embed each sentence."""
        ...


class Model2VecEmbedder:
    """potion-retrieval-32M, loaded once and held."""

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: StaticModelLike | None = None

    @property
    def spec(self) -> EngineSpec:
        """What this engine is and what it costs."""
        return SPEC

    def available(self) -> tuple[bool, str]:
        """Whether the engine can run, and if not, the reason a human can act on."""
        try:
            import model2vec  # noqa: F401
        except ImportError:
            return False, INSTALL_HINT
        return True, "ready"

    def _load(self) -> StaticModelLike:
        """Load the model on first use, then keep it."""
        if self._model is None:
            from model2vec import StaticModel

            model: StaticModelLike = StaticModel.from_pretrained(self._model_name)
            self._model = model
        return self._model

    def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed every text, in order, reporting what it consumed."""
        ready, reason = self.available()
        if not ready:
            raise EmbeddingError(f"potion unavailable: {reason}")

        started = time.perf_counter()
        encoded = self._load().encode(texts)
        vectors = [[float(value) for value in row] for row in encoded]

        return EmbeddingResult(
            vectors=vectors,
            usage=UsageRecord(
                engine=SPEC.name,
                texts=len(texts),
                tokens=sum(max(len(text) // 4, 1) for text in texts),
                cost_usd=0.0,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
        )
