"""Where the corpus lives, behind a protocol.

The protocol is the point. Day 7 puts a vector store here — chunking and embeddings
arrive behind `upsert`, and the pipeline that fills it does not change. Today's
implementation is a JSONL file, which is a legitimate store at this corpus size and
has the useful property of being readable in a diff.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from bellwether.context.documents import Document


class UpsertOutcome(StrEnum):
    """What an upsert did — the pipeline's report is a tally of these."""

    ADDED = "added"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class DocumentStore(Protocol):
    """Everything the ingestion pipeline needs from a corpus store."""

    def upsert(self, document: Document) -> UpsertOutcome:
        """Store a document, reporting whether it was new, changed, or identical."""
        ...

    def get(self, doc_id: str) -> Document | None:
        """One document by id, or None."""
        ...

    def documents(self) -> list[Document]:
        """Every stored document, ordered by id."""
        ...

    def prune(self, keep: Collection[str]) -> list[str]:
        """Delete everything whose id is not in `keep`; return what was removed."""
        ...


class InMemoryDocumentStore:
    """The reference implementation, and what the tests hold the protocol to."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}

    def upsert(self, document: Document) -> UpsertOutcome:
        """Store a document, reporting whether it was new, changed, or identical."""
        existing = self._documents.get(document.doc_id)
        if existing is None:
            self._documents[document.doc_id] = document
            return UpsertOutcome.ADDED
        if existing.content_hash == document.content_hash:
            return UpsertOutcome.UNCHANGED
        self._documents[document.doc_id] = document
        return UpsertOutcome.UPDATED

    def get(self, doc_id: str) -> Document | None:
        """One document by id, or None."""
        return self._documents.get(doc_id)

    def documents(self) -> list[Document]:
        """Every stored document, ordered by id."""
        return [self._documents[key] for key in sorted(self._documents)]

    def prune(self, keep: Collection[str]) -> list[str]:
        """Delete everything whose id is not in `keep`; return what was removed."""
        kept = set(keep)
        removed = sorted(doc_id for doc_id in self._documents if doc_id not in kept)
        for doc_id in removed:
            del self._documents[doc_id]
        return removed


class JsonlDocumentStore(InMemoryDocumentStore):
    """A corpus on disk: one JSON object per line, loaded on open."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload: dict[str, Any] = json.loads(line)
            document = Document.model_validate(payload)
            self._documents[document.doc_id] = document

    def flush(self) -> None:
        """Write the whole corpus out, ordered so the file diffs cleanly."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [document.model_dump_json() for document in self.documents()]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
