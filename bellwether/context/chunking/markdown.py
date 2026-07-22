"""Split Markdown at its headings, and carry the heading path down.

The heading path is the whole point. A paragraph reading "rejected — it puts test code
on the serving path" is nearly useless on its own; the same paragraph anchored to
`ADR-0005 › Alternatives considered` is answerable. So every section keeps its own
heading line in the text, and the anchor records the full ancestry.
"""

from __future__ import annotations

import re

from bellwether.context.chunking.window import Piece
from bellwether.context.documents import Document

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")

ANCHOR_SEPARATOR = " › "


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """Every heading as (line number, level, text), ignoring fenced code blocks."""
    found: list[tuple[int, int, str]] = []
    in_fence = False
    for index, line in enumerate(lines, start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            found.append((index, len(match.group(1)), match.group(2)))
    return found


def pieces(document: Document) -> list[Piece] | None:
    """One piece per heading section, anchored to its full heading path."""
    lines = document.content.splitlines()
    if not lines:
        return None
    headings = _headings(lines)
    if not headings:
        return None

    found: list[Piece] = []
    preamble_end = headings[0][0] - 1
    if preamble_end >= 1 and any(line.strip() for line in lines[:preamble_end]):
        # Front matter before the first heading. It has no heading to be named by,
        # which is precisely what the anchor-coverage metric is there to expose.
        found.append(
            Piece(
                text="\n".join(lines[:preamble_end]) + "\n",
                anchor=None,
                line_start=1,
                line_end=preamble_end,
            )
        )

    path: list[str] = []
    for position, (line_number, level, title) in enumerate(headings):
        del path[level - 1 :]
        path.append(title)
        end = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines)
        found.append(
            Piece(
                text="\n".join(lines[line_number - 1 : end]) + "\n",
                anchor=ANCHOR_SEPARATOR.join(path),
                line_start=line_number,
                line_end=end,
            )
        )
    return found
