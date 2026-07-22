"""Pick the strategy the document's kind deserves, and fall back rather than fail.

Every strategy may decline — a file that does not parse, a spec that is not JSON, a
Markdown document with no headings. Declining returns None and the router windows it
instead, recording `window` as the strategy that actually ran. A document that cannot
be chunked structurally still belongs in the corpus; losing it would be a far worse
outcome than chunking it badly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from bellwether.context.chunking import markdown, openapi, python_ast, window
from bellwether.context.chunking.models import Chunk, build_chunk
from bellwether.context.chunking.window import Piece
from bellwether.context.documents import Document, SourceType

Strategy = Callable[[Document], list[Piece] | None]

MARKDOWN_TYPES: frozenset[str] = frozenset(
    {"adr", "devlog", "runbook", "standards", "spec", "plan", "readme", "backlog"}
)

STRATEGY_NAMES: tuple[str, ...] = ("python_ast", "markdown", "openapi", "window")


def strategy_for(source_type: SourceType) -> tuple[str, Strategy]:
    """The strategy this kind of document should be cut with."""
    if source_type == "code":
        return "python_ast", python_ast.pieces
    if source_type == "openapi":
        return "openapi", openapi.pieces
    if source_type in MARKDOWN_TYPES:
        return "markdown", markdown.pieces
    return "window", window.pieces


def chunk_document(document: Document) -> list[Chunk]:
    """Cut one document into chunks, falling back to windowing if the strategy declines."""
    name, strategy = strategy_for(document.provenance.source_type)
    found = strategy(document)
    if found is None:
        name, found = "window", window.pieces(document)

    capped = window.cap(found)
    return [
        build_chunk(
            document=document,
            text=piece.text,
            strategy=name,
            chunk_index=index,
            anchor=piece.anchor,
            line_start=piece.line_start,
            line_end=piece.line_end,
        )
        for index, piece in enumerate(capped)
    ]


def chunk_corpus(documents: Iterable[Document]) -> list[Chunk]:
    """Cut every document, in a stable order."""
    chunks: list[Chunk] = []
    for document in sorted(documents, key=lambda item: item.doc_id):
        chunks.extend(chunk_document(document))
    return chunks
