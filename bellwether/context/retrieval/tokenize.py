"""Turn text into terms, keeping identifiers whole as well as split.

Vector search cannot find `budget_micros` — the embedding of an identifier sits
near "budget", "spending" and "cost", and no nearer the one chunk that defines the
field than to twenty that merely discuss money. Lexical search can, but only if the
tokenizer does not destroy the identifier on its way in.

So every token is emitted twice over: once whole, once in pieces. The whole is what
an exact query matches; the pieces are what a half-remembered one matches. Emitting
both is the entire reason the hybrid comparison in Day 8's eval has anything to show.
"""

from __future__ import annotations

import re

# Deliberately tiny. A long stopword list starts eating domain terms — "no", "any"
# and "all" are stopwords in prose and field names in a targeting engine.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
)

# Keeps `_`, `-`, `/` and `.` inside a token so identifiers and routes survive to
# the splitting stage, where they are handled deliberately rather than by accident.
#
# `_` is a legal *first* character too. Requiring alphanumeric there loses the whole
# form of every leading-underscore name — `__init__` would match from the `i`, strip
# to `init`, and never contribute the identifier the query actually asked for. The
# corpus ingests Python source, where that convention is everywhere.
_RAW = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\-/.]*")

# Runs of capitals (HTTPServer), Capitalised words, lowercase runs, digit runs.
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+")

_SEPARATORS = re.compile(r"[_\-/.]+")

MIN_LENGTH = 2


def _keep(token: str) -> bool:
    """A term is worth indexing if it is long enough and not a stopword."""
    return len(token) >= MIN_LENGTH and token not in STOPWORDS


def tokenize(text: str) -> list[str]:
    """Every term in `text`: each raw token whole, plus its constituent parts.

    Duplicates are kept on purpose. `budget_micros` yields three terms, so a chunk
    containing the identifier outscores one that merely mentions a budget — which is
    exactly the behaviour the identifier query category is there to verify.
    """
    terms: list[str] = []
    for match in _RAW.findall(text):
        # Strips trailing punctuation ("budget." at the end of a sentence) but never
        # underscores — those are part of the identifier, not around it.
        whole = match.lower().strip("-/.")
        if _keep(whole):
            terms.append(whole)

        parts = [piece for piece in _SEPARATORS.split(whole) if piece]
        pieces = parts if len(parts) > 1 else _CAMEL.findall(match)
        if len(pieces) <= 1:
            continue
        terms.extend(piece.lower() for piece in pieces if _keep(piece.lower()))
    return terms
