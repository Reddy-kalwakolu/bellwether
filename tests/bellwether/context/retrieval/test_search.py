# tests/bellwether/context/retrieval/test_search.py
"""One entry point, six configurations, and the engine still a parameter."""

from __future__ import annotations

from datetime import UTC, datetime

from bellwether.context.chunking.models import Chunk, build_chunk
from bellwether.context.documents import build_document
from bellwether.context.embedders import HashingEmbedder
from bellwether.context.retrieval.bm25 import BM25Index
from bellwether.context.retrieval.rerank import HeuristicReranker
from bellwether.context.retrieval.search import SearchConfig, SearchMode, SearchService
from bellwether.context.vectors import InMemoryVectorStore

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _chunk(index: int, text: str) -> Chunk:
    document = build_document(
        source_path=f"docs/adr/{index:04d}-x.md",
        source_type="adr",
        component="docs",
        title=f"ADR-{index:04d}",
        content=text,
        ingested_at=NOW,
    )
    return build_chunk(document, text, "markdown", index, f"ADR-{index:04d}", 1, 2)


def _service() -> SearchService:
    chunks = [
        _chunk(1, "The budget_micros field is enforced by the ad decision service."),
        _chunk(2, "Qdrant replaced ChromaDB because named vectors make the comparison fair."),
        _chunk(3, "Frequency capping uses Redis with a rolling window per viewer."),
        _chunk(4, "Prometheus scrapes every service and Grafana renders the dashboards."),
    ]
    embedder = HashingEmbedder()
    store = InMemoryVectorStore()
    store.ensure_collection([embedder.spec])
    store.upsert(chunks, embedder.spec.name, embedder.embed([c.text for c in chunks]).vectors)
    return SearchService(
        index=BM25Index(chunks),
        store=store,
        embedder=embedder,
        reranker=HeuristicReranker(),
    )


def test_lexical_mode_finds_the_identifier() -> None:
    hits = _service().search(
        "budget_micros", SearchConfig(mode=SearchMode.LEXICAL, engine="hashing", limit=2)
    )
    assert hits[0].chunk_id.endswith("#0001")


def test_dense_mode_returns_hits_from_the_store() -> None:
    hits = _service().search(
        "redis capping", SearchConfig(mode=SearchMode.DENSE, engine="hashing", limit=2)
    )
    assert hits


def test_hybrid_returns_at_most_the_limit() -> None:
    hits = _service().search(
        "qdrant named vectors", SearchConfig(mode=SearchMode.HYBRID, engine="hashing", limit=2)
    )
    assert len(hits) <= 2


def test_every_mode_returns_search_hits_with_provenance() -> None:
    service = _service()
    for mode in SearchMode:
        hits = service.search("budget_micros", SearchConfig(mode=mode, engine="hashing", limit=3))
        for hit in hits:
            assert hit.source_path.startswith("docs/adr/")
            assert hit.anchor is not None


def test_an_empty_query_returns_nothing_in_every_mode() -> None:
    service = _service()
    for mode in SearchMode:
        assert service.search("", SearchConfig(mode=mode, engine="hashing", limit=5)) == []


def test_hybrid_llm_without_a_reranker_falls_back_to_hybrid() -> None:
    chunks = [_chunk(1, "budget_micros is enforced here")]
    embedder = HashingEmbedder()
    store = InMemoryVectorStore()
    store.ensure_collection([embedder.spec])
    store.upsert(chunks, embedder.spec.name, embedder.embed([c.text for c in chunks]).vectors)
    service = SearchService(BM25Index(chunks), store, embedder, reranker=None)
    hits = service.search(
        "budget_micros", SearchConfig(mode=SearchMode.HYBRID_LLM, engine="hashing", limit=1)
    )
    assert len(hits) == 1


def test_results_are_reproducible_across_calls() -> None:
    service = _service()
    config = SearchConfig(mode=SearchMode.HYBRID, engine="hashing", limit=4)
    first = [hit.chunk_id for hit in service.search("service", config)]
    second = [hit.chunk_id for hit in service.search("service", config)]
    assert first == second
