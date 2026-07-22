"""What the context layer stores, and what makes it citable.

A document carries its provenance because every downstream answer has to name its
source. An agent that says "the daily budget ceiling is $2,147" without pointing at
day-05's devlog is indistinguishable from one that guessed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal[
    "code",
    "adr",
    "devlog",
    "runbook",
    "standards",
    "spec",
    "plan",
    "readme",
    "backlog",
    "config",
    "openapi",
]


def normalize(text: str) -> str:
    """Collapse line endings so the same file hashes the same on every platform."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def content_hash(text: str) -> str:
    """A labelled digest of normalised content — the unit of change detection."""
    digest = hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def line_count(text: str) -> int:
    """Lines in `text`, counting a trailing newline as a terminator, not a line."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


class Provenance(BaseModel):
    """Where a document came from, and what kind of thing it is."""

    source_path: str
    source_type: SourceType
    component: str
    title: str
    byte_size: int
    line_count: int
    generated: bool = False
    attributes: dict[str, str] = Field(default_factory=dict)


class Document(BaseModel):
    """One ingested unit of team knowledge."""

    doc_id: str
    content: str
    content_hash: str
    provenance: Provenance
    ingested_at: datetime


def build_document(
    source_path: str,
    source_type: SourceType,
    component: str,
    title: str,
    content: str,
    ingested_at: datetime,
    *,
    generated: bool = False,
    attributes: dict[str, str] | None = None,
) -> Document:
    """Assemble a document, normalising and hashing its content on the way in."""
    normalized = normalize(content)
    return Document(
        doc_id=source_path,
        content=normalized,
        content_hash=content_hash(normalized),
        provenance=Provenance(
            source_path=source_path,
            source_type=source_type,
            component=component,
            title=title,
            byte_size=len(normalized.encode("utf-8")),
            line_count=line_count(normalized),
            generated=generated,
            attributes=dict(attributes or {}),
        ),
        ingested_at=ingested_at,
    )
