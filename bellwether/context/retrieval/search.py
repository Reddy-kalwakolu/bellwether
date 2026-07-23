# bellwether/context/retrieval/search.py
"""One entry point for every retrieval configuration the eval compares.

Six modes, one code path. The comparison in Day 8's eval is only meaningful if the
five configurations differ in exactly the way their names claim and in no other way
— different candidate depths, different filters, or a different query embedding
between rows would make the table a comparison of accidents.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bellwether.context.embedders import Embedder
from bellwether.context.retrieval.bm25 import BM25Index
from bellwether.context.retrieval.fusion import reciprocal_rank_fusion, weighted_fusion
from bellwether.context.retrieval.rerank.base import Reranker
from bellwether.context.vectors import SearchHit, VectorStore


class SearchMode(StrEnum):
    """The configurations the comparison table has one row each for."""

    LEXICAL = "lexical"
    DENSE = "dense"
    HYBRID = "hybrid"
    HYBRID_WEIGHTED = "hybrid-weighted"
    HYBRID_HEURISTIC = "hybrid-heuristic"
    HYBRID_LLM = "hybrid-llm"


@dataclass(frozen=True)
class SearchConfig:
    """Everything that varies between rows of the comparison."""

    mode: SearchMode
    engine: str = "hashing"
    limit: int = 10
    candidate_depth: int = 20


class SearchService:
    """Retrieve, fuse, rerank — with every stage a parameter."""

    def __init__(
        self,
        index: BM25Index,
        store: VectorStore,
        embedder: Embedder,
        reranker: Reranker | None = None,
    ) -> None:
        self.index = index
        self.store = store
        self.embedder = embedder
        self.reranker = reranker

    def search(self, query: str, config: SearchConfig) -> list[SearchHit]:
        """The best `config.limit` chunks for `query` under `config.mode`."""
        if not query.strip():
            return []

        depth = max(config.candidate_depth, config.limit)

        if config.mode is SearchMode.LEXICAL:
            return self.index.search(query, limit=config.limit)
        if config.mode is SearchMode.DENSE:
            return self._dense(query, config.engine, config.limit)

        lexical = self.index.search(query, limit=depth)
        dense = self._dense(query, config.engine, depth)

        if config.mode is SearchMode.HYBRID_WEIGHTED:
            return weighted_fusion(dense, lexical, limit=config.limit)

        fused = reciprocal_rank_fusion([dense, lexical], limit=depth)

        if config.mode is SearchMode.HYBRID or self.reranker is None:
            return fused[: config.limit]
        return self.reranker.rerank(query, fused, limit=config.limit).hits

    def _dense(self, query: str, engine: str, limit: int) -> list[SearchHit]:
        """Embed the query with the same engine that embedded the corpus."""
        vector = self.embedder.embed([query]).vectors[0]
        return self.store.search(engine, vector, limit=limit)
