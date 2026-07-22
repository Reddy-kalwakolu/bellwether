"""Measuring whether structure-aware chunking was worth the trouble.

The honest metric here is **anchor coverage**: the share of chunks that can name what
they are. It needs no model, no network, and no judgement call, and it goes straight
at the thing that matters for retrieval — a chunk that cannot say "I am the
Alternatives section of ADR-0005" can be returned but not defended.

Naive windowing scores zero by construction. That is not a rigged comparison; it is
the actual cost of splitting on a character count, stated as a number.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from bellwether.context.chunking import window
from bellwether.context.chunking.models import Chunk, build_chunk
from bellwether.context.chunking.router import chunk_document
from bellwether.context.documents import Document

BASELINE = "naive_window"


@dataclass(frozen=True)
class StrategyStats:
    """What one strategy produced across the documents it handled."""

    strategy: str
    documents: int
    chunks: int
    mean_chars: int
    p50_chars: int
    p95_chars: int
    max_chars: int
    anchor_coverage: float


@dataclass(frozen=True)
class ComparisonReport:
    """Structure-aware chunking, measured against splitting on a character count."""

    by_strategy: dict[str, StrategyStats]
    overall: StrategyStats
    baseline: StrategyStats


def _percentile(values: list[int], fraction: float) -> int:
    """The value at `fraction` through a sorted list — no numpy needed."""
    if not values:
        return 0
    ordered = sorted(values)
    index = min(int(round(fraction * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]


def stats_for(strategy: str, chunks: Iterable[Chunk]) -> StrategyStats:
    """Summarise a set of chunks: how big, how many, and how many can name themselves."""
    collected = list(chunks)
    sizes = [len(chunk.text) for chunk in collected]
    anchored = sum(1 for chunk in collected if chunk.provenance.anchor)
    return StrategyStats(
        strategy=strategy,
        documents=len({chunk.doc_id for chunk in collected}),
        chunks=len(collected),
        mean_chars=round(sum(sizes) / len(sizes)) if sizes else 0,
        p50_chars=_percentile(sizes, 0.50),
        p95_chars=_percentile(sizes, 0.95),
        max_chars=max(sizes) if sizes else 0,
        anchor_coverage=anchored / len(collected) if collected else 0.0,
    )


def _windowed(document: Document) -> list[Chunk]:
    """The same document, cut the naive way, for the baseline."""
    return [
        build_chunk(document, piece.text, BASELINE, index, piece.anchor, *_span(piece))
        for index, piece in enumerate(window.cap(window.pieces(document)))
    ]


def _span(piece: window.Piece) -> tuple[int, int]:
    """A piece's line span, as positional arguments."""
    return piece.line_start, piece.line_end


def compare_strategies(documents: Iterable[Document]) -> ComparisonReport:
    """Cut every document both ways and report the difference."""
    collected = list(documents)

    structured: list[Chunk] = []
    baseline: list[Chunk] = []
    for document in collected:
        structured.extend(chunk_document(document))
        baseline.extend(_windowed(document))

    grouped: dict[str, list[Chunk]] = {}
    for chunk in structured:
        grouped.setdefault(chunk.provenance.strategy, []).append(chunk)

    return ComparisonReport(
        by_strategy={name: stats_for(name, chunks) for name, chunks in sorted(grouped.items())},
        overall=stats_for("all", structured),
        baseline=stats_for(BASELINE, baseline),
    )


def format_comparison(report: ComparisonReport) -> str:
    """The comparison as a table — the number that goes on the running doc."""
    header = f"{'strategy':<14}{'docs':>6}{'chunks':>8}{'mean':>7}{'p50':>7}{'p95':>7}{'anchor':>9}"
    rows = [header, "-" * len(header)]
    for stats in [*report.by_strategy.values(), report.overall, report.baseline]:
        rows.append(
            f"{stats.strategy:<14}{stats.documents:>6}{stats.chunks:>8}"
            f"{stats.mean_chars:>7}{stats.p50_chars:>7}{stats.p95_chars:>7}"
            f"{stats.anchor_coverage:>8.0%}"
        )
    return "\n".join(rows)
