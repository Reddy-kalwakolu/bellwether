# bellwether/llm/__init__.py
"""The provider registry — spec section 4.6's LLM abstraction, at its smallest."""

from __future__ import annotations

from collections.abc import Callable

from bellwether.llm.base import (
    DEFAULT_MAX_TOKENS,
    LLMClient,
    LLMError,
    LLMResponse,
    ModelSpec,
)
from bellwether.llm.claude import ClaudeClient
from bellwether.llm.gemini import GeminiClient

REGISTRY: dict[str, Callable[[], LLMClient]] = {
    "gemini": GeminiClient,
    "claude": ClaudeClient,
}

DEFAULT_CLIENT = "gemini"


def get_client(name: str) -> LLMClient:
    """Build a backend by name, raising KeyError for anything unregistered."""
    return REGISTRY[name]()


def client_specs() -> list[ModelSpec]:
    """Every backend's spec, in registry order."""
    return [get_client(name).spec for name in REGISTRY]


__all__ = [
    "DEFAULT_CLIENT",
    "DEFAULT_MAX_TOKENS",
    "REGISTRY",
    "ClaudeClient",
    "GeminiClient",
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "ModelSpec",
    "client_specs",
    "get_client",
]
