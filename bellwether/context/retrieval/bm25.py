"""Okapi BM25 over the chunked corpus — fifteen lines of scoring, no dependency.

`rank-bm25` would have done this, but the scoring function below is shorter than
that package's changelog, and a hand-written one can be read, tested and explained
on camera. It also means the identifier-aware tokenizer is not fighting somebody
else's idea of what a word is.

Indexes the *same* `list[Chunk]` the vector path embeds. A lexical index built over
different inputs than the dense one would make the Day 8 comparison meaningless in
the same way four embedding engines on four different chunk sets would have made
Day 7's meaningless.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from bellwether.context.chunking.models import Chunk
from bellwether.context.retrieval.tokenize import tokenize
from bellwether.context.vectors import SearchHit


@dataclass(frozen=True)
class BM25Params:
    """The two constants. Published defaults, not tuned against the eval set."""

    k1: float = 1.5
    b: float = 0.75


class BM25Index:
    """An in-memory inverted index. The corpus is a few hundred chunks; this is enough."""

    def __init__(self, chunks: Sequence[Chunk], params: BM25Params | None = None) -> None:
        self.params = params or BM25Params()
        self._chunks: list[Chunk] = list(chunks)
        self._frequencies: list[Counter[str]] = []
        self._lengths: list[int] = []
        self._document_frequency: Counter[str] = Counter()

        for chunk in self._chunks:
            terms = tokenize(chunk.text)
            counts = Counter(terms)
            self._frequencies.append(counts)
            self._lengths.append(len(terms))
            self._document_frequency.update(counts.keys())

        total = sum(self._lengths)
        self._average_length = total / len(self._chunks) if self._chunks else 0.0

    def __len__(self) -> int:
        """How many chunks are indexed."""
        return len(self._chunks)

    def _idf(self, term: str) -> float:
        """Inverse document frequency, in the form that cannot go negative."""
        count = self._document_frequency.get(term, 0)
        if count == 0:
            return 0.0
        total = len(self._chunks)
        return math.log(1 + (total - count + 0.5) / (count + 0.5))

    def search(
        self,
        query: str,
        limit: int = 10,
        source_types: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """The best-scoring chunks for `query`, highest first."""
        terms = tokenize(query)
        if not terms or not self._chunks:
            return []

        wanted = set(source_types or ())
        k1 = self.params.k1
        b = self.params.b

        scored: list[tuple[float, str, Chunk]] = []
        for position, chunk in enumerate(self._chunks):
            if wanted and chunk.provenance.source_type not in wanted:
                continue
            counts = self._frequencies[position]
            length = self._lengths[position]
            score = 0.0
            for term in terms:
                frequency = counts.get(term, 0)
                if frequency == 0:
                    continue
                denominator = frequency + k1 * (
                    1 - b + b * (length / self._average_length if self._average_length else 1.0)
                )
                score += self._idf(term) * (frequency * (k1 + 1)) / denominator
            if score > 0:
                scored.append((score, chunk.chunk_id, chunk))

        # Sorted by score, then chunk_id — ties must not depend on dict ordering, or
        # the eval numbers move between runs for no reason anyone can see.
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [_hit(chunk, score) for score, _, chunk in scored[:limit]]


def _hit(chunk: Chunk, score: float) -> SearchHit:
    """The same shape the vector store returns, so fusion never branches on source."""
    provenance = chunk.provenance
    return SearchHit(
        chunk_id=chunk.chunk_id,
        score=score,
        text=chunk.text,
        anchor=provenance.anchor,
        source_path=provenance.source_path,
        source_type=provenance.source_type,
        component=provenance.component,
        title=provenance.title,
    )
