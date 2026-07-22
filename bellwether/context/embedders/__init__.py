"""The engine registry — everything the desk can arm."""

from __future__ import annotations

from collections.abc import Callable

from bellwether.context.embedders.base import (
    Embedder,
    EmbeddingError,
    EmbeddingResult,
    EngineSpec,
    UsageRecord,
)
from bellwether.context.embedders.hashing import HashingEmbedder
from bellwether.context.embedders.hosted import GeminiEmbedder, VoyageEmbedder
from bellwether.context.embedders.local import Model2VecEmbedder

# Ordered best-to-baseline, which is the order the desk lays the strips out in.
REGISTRY: dict[str, Callable[[], Embedder]] = {
    "gemini": GeminiEmbedder,
    "voyage": VoyageEmbedder,
    "potion": Model2VecEmbedder,
    "hashing": HashingEmbedder,
}

DEFAULT_ENGINE = "hashing"


def get_embedder(name: str) -> Embedder:
    """Build an engine by name, raising KeyError for anything unregistered."""
    return REGISTRY[name]()


def engine_specs() -> list[EngineSpec]:
    """Every engine's spec, in desk order."""
    return [get_embedder(name).spec for name in REGISTRY]


__all__ = [
    "DEFAULT_ENGINE",
    "REGISTRY",
    "Embedder",
    "EmbeddingError",
    "EmbeddingResult",
    "EngineSpec",
    "GeminiEmbedder",
    "HashingEmbedder",
    "Model2VecEmbedder",
    "UsageRecord",
    "VoyageEmbedder",
    "engine_specs",
    "get_embedder",
]
