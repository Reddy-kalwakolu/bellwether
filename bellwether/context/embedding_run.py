"""Embed the chunked corpus with one engine, and report what it cost.

Kept separate from the pipeline because it is the unit the desk re-runs: arm an
engine, embed, compare. The pipeline owns documents; this owns vectors.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from bellwether.context.chunking.models import Chunk
from bellwether.context.embedders import Embedder
from bellwether.context.vectors import VectorStore

# Big enough to keep the request count sane, small enough that one failure does not
# discard minutes of work.
BATCH_SIZE = 128


@dataclass(frozen=True)
class EmbeddingRun:
    """One engine's pass over the corpus."""

    engine: str
    label: str
    dimensions: int
    chunks: int
    tokens: int
    cost_usd: float
    wall_ms: float
    stored: int

    @property
    def chunks_per_second(self) -> float:
        """Throughput, for the desk's speed meter."""
        if self.wall_ms <= 0:
            return 0.0
        return self.chunks / (self.wall_ms / 1000)


def embed_corpus(
    chunks: Sequence[Chunk],
    embedder: Embedder,
    store: VectorStore,
    batch_size: int = BATCH_SIZE,
) -> EmbeddingRun:
    """Embed every chunk with one engine and store the vectors under its name."""
    spec = embedder.spec
    tokens = 0
    cost = 0.0
    wall = 0.0
    stored = 0

    for start in range(0, len(chunks), batch_size):
        batch = list(chunks[start : start + batch_size])
        result = embedder.embed([chunk.text for chunk in batch])
        tokens += result.usage.tokens
        cost += result.usage.cost_usd
        wall += result.usage.latency_ms
        stored += store.upsert(batch, spec.name, result.vectors)

    return EmbeddingRun(
        engine=spec.name,
        label=spec.label,
        dimensions=spec.dimensions,
        chunks=len(chunks),
        tokens=tokens,
        cost_usd=cost,
        wall_ms=wall,
        stored=stored,
    )


def format_runs(runs: Sequence[EmbeddingRun]) -> str:
    """The engine comparison as a table — the numbers that go on the running doc."""
    header = (
        f"{'engine':<10}{'dims':>6}{'chunks':>8}{'tokens':>9}"
        f"{'cost USD':>11}{'wall s':>9}{'chunks/s':>10}"
    )
    rows = [header, "-" * len(header)]
    for run in runs:
        rows.append(
            f"{run.label.lower():<10}{run.dimensions:>6}{run.chunks:>8}{run.tokens:>9}"
            f"{run.cost_usd:>11.4f}{run.wall_ms / 1000:>9.1f}{run.chunks_per_second:>10.1f}"
        )
    total = sum(run.cost_usd for run in runs)
    rows.append("-" * len(header))
    rows.append(f"{'total':<10}{'':>6}{'':>8}{'':>9}{total:>11.4f}")
    return "\n".join(rows)
