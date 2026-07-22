"""Embedding the corpus with one engine, and the bill that comes back."""

from __future__ import annotations

from datetime import UTC, datetime

from bellwether.context.chunking.models import Chunk, build_chunk
from bellwether.context.documents import build_document
from bellwether.context.embedders import HashingEmbedder
from bellwether.context.embedding_run import embed_corpus, format_runs
from bellwether.context.vectors import InMemoryVectorStore

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _chunks(count: int) -> list[Chunk]:
    document = build_document(
        source_path="docs/adr/0005-x.md",
        source_type="adr",
        component="docs",
        title="ADR-0005",
        content="budget pacing",
        ingested_at=NOW,
    )
    return [
        build_chunk(
            document, f"chunk number {index} about budget pacing", "markdown", index, None, 1, 2
        )
        for index in range(count)
    ]


def test_every_chunk_is_embedded_and_stored() -> None:
    store = InMemoryVectorStore()
    run = embed_corpus(_chunks(10), HashingEmbedder(), store)

    assert run.chunks == 10
    assert run.stored == 10
    assert run.engine == "hashing"
    assert run.dimensions == 256
    assert store.stats().engines["hashing"] == 10


def test_batching_does_not_lose_or_duplicate_chunks() -> None:
    store = InMemoryVectorStore()
    run = embed_corpus(_chunks(300), HashingEmbedder(), store, batch_size=128)

    assert run.chunks == run.stored == 300
    assert store.stats().points == 300


def test_the_run_reports_tokens_and_a_cost() -> None:
    run = embed_corpus(_chunks(5), HashingEmbedder(), InMemoryVectorStore())
    assert run.tokens > 0
    assert run.cost_usd == 0.0
    assert run.wall_ms >= 0


def test_throughput_survives_a_zero_length_run() -> None:
    run = embed_corpus([], HashingEmbedder(), InMemoryVectorStore())
    assert run.chunks == 0
    assert run.chunks_per_second == 0.0


def test_the_comparison_prints_every_engine_and_a_total() -> None:
    runs = [embed_corpus(_chunks(3), HashingEmbedder(), InMemoryVectorStore())]
    text = format_runs(runs)
    assert "engine" in text
    assert "hashing" in text
    assert "total" in text
