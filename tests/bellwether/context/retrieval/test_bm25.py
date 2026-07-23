"""Lexical retrieval — the half of the system that can find an identifier."""

from __future__ import annotations

from datetime import UTC, datetime

from bellwether.context.chunking.models import Chunk, build_chunk
from bellwether.context.documents import build_document
from bellwether.context.retrieval.bm25 import BM25Index

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _chunk(index: int, text: str, source_type: str = "adr") -> Chunk:
    document = build_document(
        source_path=f"docs/adr/{index:04d}-x.md",
        source_type=source_type,  # type: ignore[arg-type]
        component="docs",
        title=f"ADR-{index:04d}",
        content=text,
        ingested_at=NOW,
    )
    return build_chunk(document, text, "markdown", index, f"ADR-{index:04d}", 1, 2)


def _corpus() -> list[Chunk]:
    return [
        _chunk(1, "The campaign budget_micros field is enforced in the decision service."),
        _chunk(2, "Budgets and spending are tracked daily against a cap."),
        _chunk(3, "Qdrant was chosen over ChromaDB because of named vectors."),
        _chunk(4, "Frequency capping uses Redis with a rolling window."),
    ]


def test_finds_the_exact_identifier_first() -> None:
    index = BM25Index(_corpus())
    hits = index.search("budget_micros", limit=2)
    assert hits[0].chunk_id.endswith("#0001")


def test_returns_search_hits_carrying_provenance() -> None:
    index = BM25Index(_corpus())
    hit = index.search("named vectors", limit=1)[0]
    assert hit.anchor == "ADR-0003"
    assert hit.source_path == "docs/adr/0003-x.md"
    assert hit.source_type == "adr"


def test_scores_are_positive_and_descending() -> None:
    index = BM25Index(_corpus())
    hits = index.search("budget", limit=4)
    scores = [hit.score for hit in hits]
    assert all(score > 0 for score in scores)
    assert scores == sorted(scores, reverse=True)


def test_a_term_in_no_document_returns_nothing() -> None:
    index = BM25Index(_corpus())
    assert index.search("kubernetes") == []


def test_empty_query_returns_nothing() -> None:
    index = BM25Index(_corpus())
    assert index.search("") == []


def test_respects_the_limit() -> None:
    index = BM25Index(_corpus())
    assert len(index.search("budget spending cap decision", limit=2)) == 2


def test_filters_by_source_type() -> None:
    corpus = [*_corpus(), _chunk(5, "budget_micros in code", source_type="code")]
    index = BM25Index(corpus)
    hits = index.search("budget_micros", limit=10, source_types=["code"])
    assert [hit.source_type for hit in hits] == ["code"]


def test_ranking_is_deterministic_across_builds() -> None:
    first = BM25Index(_corpus()).search("budget", limit=4)
    second = BM25Index(_corpus()).search("budget", limit=4)
    assert [hit.chunk_id for hit in first] == [hit.chunk_id for hit in second]


def test_length_normalisation_does_not_favour_long_chunks() -> None:
    short = _chunk(1, "budget_micros")
    padding = " ".join(["irrelevant filler prose"] * 60)
    long = _chunk(2, f"budget_micros {padding}")
    hits = BM25Index([short, long]).search("budget_micros", limit=2)
    assert hits[0].chunk_id.endswith("#0001")


def test_len_reports_the_corpus_size() -> None:
    assert len(BM25Index(_corpus())) == 4
