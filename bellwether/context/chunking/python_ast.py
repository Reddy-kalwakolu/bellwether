"""Split Python where Python already has boundaries.

A function is a unit of meaning; half a function is not. So the split points come
from the parse tree rather than a character count, and each piece is anchored to the
dotted symbol path a reader would use to find it — `substrate.traffic_simulator.
driver.tick`, not "chunk 7".

Decorators count as part of the symbol they decorate: `@app.post("/ad-request")` is
often the single most retrievable line in the file, and separating it from the handler
would put the route and its implementation in different chunks.
"""

from __future__ import annotations

import ast

from bellwether.context.chunking.window import Piece
from bellwether.context.documents import Document

_SYMBOL_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _module_path(document: Document) -> str:
    """The dotted import path, so an anchor matches how the code is referenced."""
    module = document.provenance.attributes.get("module")
    if module:
        return module
    return document.provenance.source_path.removesuffix(".py").replace("/", ".")


def _span(node: ast.stmt) -> tuple[int, int]:
    """The 1-indexed line span of a node, including any decorators above it."""
    start = node.lineno
    decorators = getattr(node, "decorator_list", [])
    for decorator in decorators:
        start = min(start, decorator.lineno)
    return start, node.end_lineno or node.lineno


def pieces(document: Document) -> list[Piece] | None:
    """One piece per top-level symbol, plus one for module-level code.

    Returns None if the file does not parse — a half-written module must fall back
    to windowing rather than vanish from the corpus.
    """
    try:
        tree = ast.parse(document.content)
    except SyntaxError:
        return None

    lines = document.content.splitlines()
    module_path = _module_path(document)
    found: list[Piece] = []
    claimed: set[int] = set()

    for node in tree.body:
        if not isinstance(node, _SYMBOL_NODES):
            continue
        start, end = _span(node)
        claimed.update(range(start, end + 1))
        found.append(
            Piece(
                text="\n".join(lines[start - 1 : end]) + "\n",
                anchor=f"{module_path}.{node.name}",
                line_start=start,
                line_end=end,
            )
        )

    remainder = [index for index in range(1, len(lines) + 1) if index not in claimed]
    if remainder and any(lines[index - 1].strip() for index in remainder):
        # Imports, constants, and the module docstring. Retrieved on questions like
        # "what does this module depend on", which no symbol chunk can answer.
        found.append(
            Piece(
                text="\n".join(lines[index - 1] for index in remainder) + "\n",
                anchor=f"{module_path} · module scope",
                line_start=remainder[0],
                line_end=remainder[-1],
            )
        )

    if not found:
        return None
    return sorted(found, key=lambda piece: piece.line_start)
