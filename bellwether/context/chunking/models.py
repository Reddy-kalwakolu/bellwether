"""What a chunk is, and what makes it citable.

A whole document is the wrong unit for retrieval — an 851-line OpenAPI contract
answers no question usefully. A chunk is the right unit, but only if it can still
say where it came from and what it is. Hence the anchor: the symbol, heading, or
route that names this region. A chunk with no anchor is one retrieval can find but
cannot explain, and the strategy comparison measures exactly that.
"""

from __future__ import annotations

from pydantic import BaseModel

from bellwether.context.documents import Document, SourceType, content_hash, normalize


class ChunkProvenance(BaseModel):
    """Where a chunk came from — inherited from its document, plus its own region."""

    doc_id: str
    source_path: str
    source_type: SourceType
    component: str
    title: str
    strategy: str
    chunk_index: int
    anchor: str | None
    line_start: int
    line_end: int


class Chunk(BaseModel):
    """One retrievable region of one document."""

    chunk_id: str
    doc_id: str
    text: str
    content_hash: str
    provenance: ChunkProvenance


def build_chunk(
    document: Document,
    text: str,
    strategy: str,
    chunk_index: int,
    anchor: str | None,
    line_start: int,
    line_end: int,
) -> Chunk:
    """Cut one chunk out of `document`, carrying its provenance down."""
    normalized = normalize(text)
    if not normalized.strip():
        raise ValueError(f"empty chunk at index {chunk_index} of {document.doc_id}")
    if line_end < line_start:
        raise ValueError(f"line span {line_start}-{line_end} is inverted in {document.doc_id}")

    return Chunk(
        chunk_id=f"{document.doc_id}#{chunk_index:04d}",
        doc_id=document.doc_id,
        text=normalized,
        content_hash=content_hash(normalized),
        provenance=ChunkProvenance(
            doc_id=document.doc_id,
            source_path=document.provenance.source_path,
            source_type=document.provenance.source_type,
            component=document.provenance.component,
            title=document.provenance.title,
            strategy=strategy,
            chunk_index=chunk_index,
            anchor=anchor,
            line_start=line_start,
            line_end=line_end,
        ),
    )
