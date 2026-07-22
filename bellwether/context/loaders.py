"""One file in, one document out — with the provenance its kind deserves.

The title and the component are what a citation shows a human, so they are extracted
rather than guessed: an ADR's title is its heading, a module's title is the first line
of its docstring, and a substrate module belongs to the service that would page you if
it broke.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime

from bellwether.context.discovery import DiscoveredFile
from bellwether.context.documents import Document, SourceType, build_document, normalize

MARKDOWN_TYPES: frozenset[str] = frozenset(
    {"adr", "devlog", "runbook", "standards", "spec", "plan", "readme", "backlog"}
)

_ADR_NUMBER = re.compile(r"^(\d{4})-")
_DAY_NUMBER = re.compile(r"day-?(\d{1,2})")


def component_for(source_path: str) -> str:
    """Which part of the system a document describes."""
    segments = source_path.split("/")
    if segments[0] == "substrate" and len(segments) > 2:
        return segments[1].replace("_", "-")
    if segments[0] in {"platform", "docs", "infra"}:
        return segments[0]
    if source_path == "docker-compose.yml":
        return "infra"
    return "repo"


def _first_heading(content: str) -> str | None:
    """The first level-one markdown heading, if the document has one."""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _module_summary(content: str) -> str | None:
    """The first line of a module docstring, if the file parses and has one."""
    try:
        docstring = ast.get_docstring(ast.parse(content))
    except SyntaxError:
        return None
    if not docstring:
        return None
    return docstring.strip().splitlines()[0].strip() or None


def title_for(source_path: str, source_type: SourceType, content: str) -> str:
    """A human-readable name for the document, for use in a citation."""
    filename = source_path.rsplit("/", 1)[-1]
    if source_type in MARKDOWN_TYPES:
        return _first_heading(content) or filename
    if source_type == "code":
        return _module_summary(content) or filename
    return filename


def attributes_for(source_path: str, source_type: SourceType) -> dict[str, str]:
    """The extra provenance fields that only this kind of document has."""
    filename = source_path.rsplit("/", 1)[-1]
    if source_type == "adr":
        adr = _ADR_NUMBER.match(filename)
        return {"adr_number": adr.group(1)} if adr else {}
    if source_type in {"devlog", "plan"}:
        day = _DAY_NUMBER.search(filename)
        return {"day": day.group(1).zfill(2)} if day else {}
    if source_type == "code":
        return {"module": source_path.removesuffix(".py").replace("/", ".")}
    return {}


def load(found: DiscoveredFile, ingested_at: datetime) -> Document:
    """Read one discovered file and turn it into a document."""
    content = normalize(found.path.read_text(encoding="utf-8"))
    return build_document(
        source_path=found.source_path,
        source_type=found.source_type,
        component=component_for(found.source_path),
        title=title_for(found.source_path, found.source_type, content),
        content=content,
        ingested_at=ingested_at,
        attributes=attributes_for(found.source_path, found.source_type),
    )
