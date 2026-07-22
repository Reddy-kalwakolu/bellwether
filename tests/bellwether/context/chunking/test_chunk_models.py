"""A chunk is a citable region of a document, not a slice of anonymous text."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bellwether.context.chunking.models import build_chunk
from bellwether.context.documents import build_document, content_hash

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)

DOCUMENT = build_document(
    source_path="docs/adr/0005-real-failures-not-mocked.md",
    source_type="adr",
    component="docs",
    title="ADR-0005 — Real failures, not mocked ones",
    content="# ADR-0005\n\nbody\n",
    ingested_at=NOW,
    attributes={"adr_number": "0005"},
)


def test_a_chunk_id_points_at_a_region_a_human_can_open() -> None:
    chunk = build_chunk(DOCUMENT, "text", "markdown", 0, "ADR-0005", 1, 3)
    assert chunk.chunk_id == "docs/adr/0005-real-failures-not-mocked.md#0000"


def test_chunk_ids_are_zero_padded_so_they_sort() -> None:
    chunk = build_chunk(DOCUMENT, "text", "markdown", 12, None, 1, 2)
    assert chunk.chunk_id.endswith("#0012")


def test_a_chunk_inherits_the_documents_provenance() -> None:
    chunk = build_chunk(DOCUMENT, "text", "markdown", 0, "ADR-0005", 1, 3)
    provenance = chunk.provenance
    assert chunk.doc_id == DOCUMENT.doc_id
    assert provenance.source_path == DOCUMENT.provenance.source_path
    assert provenance.source_type == "adr"
    assert provenance.component == "docs"
    assert provenance.title == DOCUMENT.provenance.title


def test_a_chunk_adds_its_own_provenance_on_top() -> None:
    chunk = build_chunk(DOCUMENT, "text", "markdown", 3, "Decisions › ADR-0005", 10, 22)
    assert chunk.provenance.strategy == "markdown"
    assert chunk.provenance.chunk_index == 3
    assert chunk.provenance.anchor == "Decisions › ADR-0005"
    assert (chunk.provenance.line_start, chunk.provenance.line_end) == (10, 22)


def test_chunk_text_is_hashed_the_same_way_documents_are() -> None:
    chunk = build_chunk(DOCUMENT, "a\r\nb\n", "markdown", 0, None, 1, 2)
    assert chunk.text == "a\nb\n"
    assert chunk.content_hash == content_hash("a\nb\n")


def test_an_anchor_is_optional_because_some_content_has_no_structure() -> None:
    assert build_chunk(DOCUMENT, "text", "window", 0, None, 1, 2).provenance.anchor is None


def test_an_empty_chunk_is_a_bug_not_a_document() -> None:
    with pytest.raises(ValueError, match="empty"):
        build_chunk(DOCUMENT, "   \n  ", "window", 0, None, 1, 2)


def test_a_line_span_always_covers_at_least_one_line() -> None:
    with pytest.raises(ValueError, match="line span"):
        build_chunk(DOCUMENT, "text", "window", 0, None, 5, 4)


def test_two_chunks_of_the_same_document_are_distinguishable() -> None:
    first = build_chunk(DOCUMENT, "one", "markdown", 0, None, 1, 1)
    second = build_chunk(DOCUMENT, "two", "markdown", 1, None, 2, 2)
    assert first.chunk_id != second.chunk_id
    assert first.content_hash != second.content_hash
