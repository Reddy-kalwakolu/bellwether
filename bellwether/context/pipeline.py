"""discover → load → hash → upsert → prune.

The pipeline itself is boring on purpose. Every interesting decision lives in a module
it calls: what belongs in the corpus (discovery), what provenance a document carries
(loaders), where it goes (store). That is what lets Day 7 swap a vector store in
underneath without editing this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bellwether.context.discovery import discover
from bellwether.context.documents import Document
from bellwether.context.loaders import load
from bellwether.context.openapi import openapi_documents
from bellwether.context.store import DocumentStore, UpsertOutcome


@dataclass(frozen=True)
class IngestionReport:
    """What one run of the pipeline did."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: list[str] = field(default_factory=list)
    documents: int = 0
    bytes_ingested: int = 0
    by_source_type: dict[str, int] = field(default_factory=dict)


def ingest(
    root: Path,
    store: DocumentStore,
    ingested_at: datetime | None = None,
    *,
    include_openapi: bool = True,
) -> IngestionReport:
    """Ingest the corpus under `root` into `store`, returning what changed."""
    now = ingested_at or datetime.now(UTC)

    corpus: list[Document] = [load(found, now) for found in discover(root)]
    if include_openapi:
        corpus.extend(openapi_documents(now))
    corpus.sort(key=lambda document: document.doc_id)

    tally = dict.fromkeys(UpsertOutcome, 0)
    by_source_type: dict[str, int] = {}
    bytes_ingested = 0
    for document in corpus:
        tally[store.upsert(document)] += 1
        source_type = document.provenance.source_type
        by_source_type[source_type] = by_source_type.get(source_type, 0) + 1
        bytes_ingested += document.provenance.byte_size

    removed = store.prune({document.doc_id for document in corpus})

    return IngestionReport(
        added=tally[UpsertOutcome.ADDED],
        updated=tally[UpsertOutcome.UPDATED],
        unchanged=tally[UpsertOutcome.UNCHANGED],
        removed=removed,
        documents=len(corpus),
        bytes_ingested=bytes_ingested,
        by_source_type=dict(sorted(by_source_type.items())),
    )


def format_report(report: IngestionReport) -> str:
    """The report as a human reads it — the line that goes in the video."""
    lines = [
        f"corpus:    {report.documents} documents, {report.bytes_ingested / 1024:.1f} KiB",
        f"changes:   {report.added} added, {report.updated} updated, "
        f"{report.unchanged} unchanged, {len(report.removed)} removed",
        "by source type:",
    ]
    width = max((len(name) for name in report.by_source_type), default=0)
    lines.extend(
        f"  {name.ljust(width)}  {count:>4}" for name, count in report.by_source_type.items()
    )
    lines.extend(f"  removed: {doc_id}" for doc_id in report.removed)
    return "\n".join(lines)
