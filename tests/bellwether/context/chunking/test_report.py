"""Does structure-aware chunking actually buy anything? Measured, not asserted."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bellwether.context.chunking.report import compare_strategies, format_comparison, stats_for
from bellwether.context.chunking.router import chunk_document
from bellwether.context.documents import build_document
from bellwether.context.pipeline import ingest
from bellwether.context.store import InMemoryDocumentStore

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[4]

ADR = build_document(
    source_path="docs/adr/0005-x.md",
    source_type="adr",
    component="docs",
    title="ADR-0005",
    content=(
        "# ADR-0005\n\nContext.\n\n## Decision\n\nDo the thing.\n\n## Consequences\n\nIt breaks.\n"
    ),
    ingested_at=NOW,
)


def test_stats_describe_size_and_how_many_chunks_can_name_themselves() -> None:
    stats = stats_for("markdown", chunk_document(ADR))
    assert stats.strategy == "markdown"
    assert stats.documents == 1
    assert stats.chunks == 3
    assert stats.anchor_coverage == 1.0
    assert stats.max_chars >= stats.p50_chars


def test_percentiles_survive_a_single_chunk() -> None:
    stats = stats_for("window", chunk_document(ADR)[:1])
    assert stats.p50_chars == stats.p95_chars == stats.max_chars


def test_empty_input_does_not_divide_by_zero() -> None:
    stats = stats_for("window", [])
    assert (stats.chunks, stats.mean_chars, stats.anchor_coverage) == (0, 0, 0.0)


def test_the_baseline_cannot_name_anything_it_produces() -> None:
    report = compare_strategies([ADR])
    # This is the cost of splitting on a character count, stated as a number.
    assert report.baseline.anchor_coverage == 0.0


def test_structure_aware_chunking_beats_the_baseline_on_the_real_corpus() -> None:
    store = InMemoryDocumentStore()
    ingest(REPO_ROOT, store)

    report = compare_strategies(store.documents())

    assert report.overall.anchor_coverage > 0.85
    assert report.baseline.anchor_coverage == 0.0
    assert report.overall.chunks > report.baseline.chunks
    assert {"python_ast", "markdown", "openapi"} <= set(report.by_strategy)


def test_structural_chunks_are_smaller_than_whole_windows() -> None:
    store = InMemoryDocumentStore()
    ingest(REPO_ROOT, store)

    report = compare_strategies(store.documents())

    # More, smaller, named pieces is the entire trade being made here.
    assert report.overall.p50_chars < report.baseline.p50_chars


def test_the_comparison_prints_as_a_table_a_human_can_read() -> None:
    text = format_comparison(compare_strategies([ADR]))
    assert "strategy" in text
    assert "anchor" in text
    assert "markdown" in text
    assert "naive_window" in text
