"""Split an API contract the way a person asks about one: one endpoint at a time.

Nobody asks "what is in campaign-service's OpenAPI document". They ask "what does
POST /campaigns accept" — so an operation is the unit, anchored to `POST /campaigns`,
carrying its summary, parameters, request body and response codes together.

The operation text is re-serialised rather than sliced out of the source, because a
JSON object cannot be cut on line boundaries without becoming invalid. The line span
is therefore a best-effort pointer at the path key in the source document: honest
about where to look, not a claim to have quoted it verbatim.
"""

from __future__ import annotations

import json
from typing import Any

from bellwether.context.chunking.window import Piece
from bellwether.context.documents import Document

_METHODS = frozenset({"get", "put", "post", "delete", "patch", "options", "head", "trace"})


def _line_of(lines: list[str], needle: str) -> int:
    """The first line mentioning `needle`, or 1 if the source does not show it."""
    for index, line in enumerate(lines, start=1):
        if needle in line:
            return index
    return 1


def _render(payload: dict[str, Any]) -> str:
    """Stable JSON text for one operation or schema."""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def pieces(document: Document) -> list[Piece] | None:
    """One piece per operation, plus one per component schema."""
    try:
        parsed: object = json.loads(document.content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    lines = document.content.splitlines()
    title = str(parsed.get("info", {}).get("title", document.provenance.title))
    found: list[Piece] = []

    paths = parsed.get("paths")
    if isinstance(paths, dict):
        for route, operations in sorted(paths.items()):
            if not isinstance(operations, dict):
                continue
            anchor_line = _line_of(lines, f'"{route}"')
            for method, operation in sorted(operations.items()):
                if method.lower() not in _METHODS:
                    continue
                body = _render({"service": title, method.upper(): {route: operation}})
                found.append(
                    Piece(
                        text=body,
                        anchor=f"{method.upper()} {route}",
                        line_start=anchor_line,
                        line_end=anchor_line,
                    )
                )

    schemas = parsed.get("components", {})
    if isinstance(schemas, dict):
        models = schemas.get("schemas")
        if isinstance(models, dict):
            for name, schema in sorted(models.items()):
                anchor_line = _line_of(lines, f'"{name}"')
                found.append(
                    Piece(
                        text=_render({"service": title, "schema": {name: schema}}),
                        anchor=f"schema {name}",
                        line_start=anchor_line,
                        line_end=anchor_line,
                    )
                )

    return found or None
