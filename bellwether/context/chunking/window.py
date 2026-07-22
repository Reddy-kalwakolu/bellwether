"""The unit every strategy produces, and the size cap every strategy runs through.

A `Piece` is a region a strategy found — text, the anchor naming it, and the lines it
came from — before it becomes a `Chunk` with an index. Keeping that intermediate step
means the strategies stay pure and ignorant of numbering, and the cap below can split
an oversized region without any strategy knowing.

The cap matters more than it looks. A 400-line function and a 900-line heading section
are both real, and both too big to embed usefully. When one is split, the parts keep
the parent's anchor with a `part N` suffix — losing the anchor at exactly the moment
the text gets harder to recognise would be the worst possible trade.
"""

from __future__ import annotations

from dataclasses import dataclass

from bellwether.context.documents import Document

# ~500 tokens. Comfortably inside every engine's per-text limit, including
# gemini-embedding-001's 2048-token input cap, with room for the anchor.
MAX_CHARS = 2000
OVERLAP_LINES = 2


@dataclass(frozen=True)
class Piece:
    """A region a strategy found, before it is numbered."""

    text: str
    anchor: str | None
    line_start: int
    line_end: int


def _split_oversized(piece: Piece, max_chars: int, overlap_lines: int) -> list[Piece]:
    """Break one oversized piece on line boundaries, keeping its anchor."""
    lines = piece.text.splitlines()
    parts: list[Piece] = []
    start = 0
    while start < len(lines):
        end = start
        size = 0
        while end < len(lines) and (size == 0 or size + len(lines[end]) + 1 <= max_chars):
            size += len(lines[end]) + 1
            end += 1
        part_lines = lines[start:end]
        anchor = piece.anchor
        label = f"{anchor} · part {len(parts) + 1}" if anchor else None
        parts.append(
            Piece(
                text="\n".join(part_lines) + "\n",
                anchor=label,
                line_start=piece.line_start + start,
                line_end=piece.line_start + end - 1,
            )
        )
        if end >= len(lines):
            break
        start = max(end - overlap_lines, start + 1)
    return parts


def cap(
    pieces: list[Piece], max_chars: int = MAX_CHARS, overlap_lines: int = OVERLAP_LINES
) -> list[Piece]:
    """Split any piece over `max_chars`, dropping any that are only whitespace."""
    capped: list[Piece] = []
    for piece in pieces:
        if not piece.text.strip():
            continue
        if len(piece.text) <= max_chars:
            capped.append(piece)
            continue
        capped.extend(_split_oversized(piece, max_chars, overlap_lines))
    return capped


def pieces(document: Document) -> list[Piece]:
    """The fallback strategy: fixed windows, no anchor, no structure claimed.

    This is what the comparison measures everything else against. It never fails,
    and it never explains what it found.
    """
    lines = document.content.splitlines()
    if not lines:
        return []
    whole = Piece(
        text=document.content,
        anchor=None,
        line_start=1,
        line_end=len(lines),
    )
    if len(whole.text) <= MAX_CHARS:
        return [whole]
    return _split_oversized(whole, MAX_CHARS, OVERLAP_LINES)
