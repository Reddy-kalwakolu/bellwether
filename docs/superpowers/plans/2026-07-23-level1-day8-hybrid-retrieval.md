# BELLWETHER Level 1 / Day 8 — Hybrid retrieval, reranking, and an honest number

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add BM25 lexical retrieval over Day 7's 585 chunks, fuse it with vector search using Reciprocal Rank Fusion, rerank the result behind a two-implementation protocol (free heuristic + LLM), and measure all five configurations against a pooled-judgement gold set so the day publishes a number no configuration could rig.

**Architecture:** Retrieval is a pipeline of pure stages — `retrieve → fuse → rerank` — each taking and returning `list[SearchHit]`, Day 7's existing type. `retrieval/` holds the lexical index, the fusion functions, and the orchestrator; `retrieval/rerank/` holds a `Reranker` protocol shaped exactly like Day 7's `Embedder`; `bellwether/llm/` is the first piece of spec §4.6 — a thin provider protocol with an injected transport, not a framework. `eval/` owns the gold set, the pooling harness, and the metrics. Nothing in the chain knows which engine produced the dense side or which backend reranked, which is what makes the comparison possible.

**The load-bearing idea:** *a retrieval system that has not been measured against an adversarial answer key is a claim, not a result.* Several decisions below are deliberately harder than the alternative for exactly this reason.

**Tech Stack:** Python 3.11+, Pydantic v2, httpx (already present), Qdrant (Docker, port 6333), pytest. No new required dependency — BM25 is hand-written.

## Global Constraints

- Python 3.11+; `mypy --strict` clean on `tests substrate platform bellwether`; ruff + `ruff format --check` clean (line length 100); conventional commits
- **Gates run unpiped, each on its own line.** A piped gate exits with `tail`'s status. This was broken on Day 4, and broken again on Day 7 by the author of the Day 4 note. Never `uv run mypy ... | tail -2 && git commit`.
- **Tests stay hermetic.** The full suite passes with Docker stopped and **no API keys set**. No test calls Gemini, Anthropic, Qdrant, or the network. `HashingEmbedder` + `InMemoryVectorStore` + fake transports throughout.
- **No new required dependency.** BM25 is hand-written; the LLM client uses `httpx`, already a dependency.
- **A missing API key disables a backend, it never crashes.** `available() -> tuple[bool, str]` on every backend; the reason must be actionable by a human.
- Files read/written with explicit `encoding="utf-8"`. `.env` is read as `utf-8-sig` by the existing `load_env_file` (Day 7 note 2 — PowerShell's BOM presented as a missing credential).
- Anything that waits in production takes an **injected clock** (Day 7 note 4 — a retry test slept for three real minutes).
- **No model id hardcoded in logic.** Model ids live in a spec dataclass with an environment override (spec §4.6: stale model names in a portfolio repo signal copy-paste planning).
- Published cost figures must be **numbers a real run printed**, never estimates.
- Every directory under `tests/` needs an `__init__.py`; `uv` is invoked as `python -m uv`.
- **Do not change `Chunk`, `SearchHit`, or the `VectorStore` protocol.** A retrieval layer that reshapes its store's return type has the wrong boundary. If a field turns out to be missing, record it as a finding.
- Definition of done: ADR-0009, ADR-0010, `docs/devlog/day-08.md`, `docs/site/index.html` updated (DAY 08, pod head `8 shipped · 22 queued`, Day 7 segment gains `nohead`, tracker row 08 SHIPPED, Level 1 section extended), and `data/gold/day08-retrieval.json` committed.

## What already exists (do not redefine these)

| Symbol | Import from | Shape |
|---|---|---|
| `Chunk` | `bellwether.context.chunking.models` | Pydantic: `chunk_id`, `doc_id`, `text`, `content_hash`, `provenance` |
| `ChunkProvenance` | same | `anchor: str \| None`, `source_type`, `source_path`, `component`, `title`, `strategy`, `chunk_index`, `line_start`, `line_end` |
| `build_chunk(document, text, strategy, chunk_index, anchor, line_start, line_end)` | same | Returns `Chunk` |
| `SearchHit` | `bellwether.context.vectors` | **Frozen dataclass**: `chunk_id`, `score`, `text`, `anchor`, `source_path`, `source_type`, `component`, `title` |
| `VectorStore`, `InMemoryVectorStore` | same | `.search(engine, vector, limit, source_types) -> list[SearchHit]` |
| `Embedder` | `bellwether.context.embedders` | `.spec`, `.available() -> tuple[bool, str]`, `.embed(list[str]) -> EmbeddingResult` |
| `EmbeddingResult` | `bellwether.context.embedders.base` | `.vectors: list[list[float]]`, `.usage: UsageRecord` |
| `UsageRecord` | same | `engine`, `texts`, `tokens`, `cost_usd`, `latency_ms` |
| `HttpPost` | same | `__call__(url, payload, headers) -> tuple[int, dict[str, Any]]` |
| `cost_for(tokens, cost_per_million_tokens) -> float` | same | Reuse; do not reimplement |
| `HashingEmbedder` | `bellwether.context.embedders` | Deterministic, 256-dim, free. The CI default |
| `chunk_corpus(documents) -> list[Chunk]` | `bellwether.context.chunking.router` | |
| `JsonlDocumentStore(path)` | `bellwether.context.store` | `.documents() -> list[Document]` |
| `embed_corpus(chunks, embedder, store, batch_size)` | `bellwether.context.embedding_run` | Exists but **is not wired to any CLI** — Task 7 fixes that |
| `load_env_file(path=None)` | `bellwether.context.config` | Reads `.env` as `utf-8-sig` |

`SearchHit` is a **frozen** dataclass — to change a score, use `dataclasses.replace(hit, score=new)`.

## File Structure

| File | Responsibility |
|---|---|
| `bellwether/context/retrieval/__init__.py` | Re-exports the public retrieval surface |
| `bellwether/context/retrieval/tokenize.py` | The identifier-aware tokenizer — originals *and* parts |
| `bellwether/context/retrieval/bm25.py` | Okapi BM25 index over `Chunk`s, returning `SearchHit` |
| `bellwether/context/retrieval/fusion.py` | Reciprocal Rank Fusion + the weighted alternative |
| `bellwether/context/retrieval/search.py` | `SearchService` — one entry point, five configurations |
| `bellwether/context/retrieval/rerank/base.py` | `Reranker` protocol, `RerankerSpec`, `RerankResult`, `RerankError` |
| `bellwether/context/retrieval/rerank/heuristic.py` | Free deterministic reranker. The CI default |
| `bellwether/context/retrieval/rerank/llm.py` | LLM reranker over `bellwether.llm`, structured output |
| `bellwether/llm/base.py` | `LLMClient` protocol, `ModelSpec`, `LLMResponse`, `LLMError`, registry |
| `bellwether/llm/gemini.py` | Gemini backend over HTTP, injected transport |
| `bellwether/llm/claude.py` | Claude backend — written and hermetically tested, never run live |
| `bellwether/eval/metrics.py` | nDCG@10, recall@10, MRR — pure functions |
| `bellwether/eval/gold.py` | `GoldQuery`, `GoldSet`, load + validate |
| `bellwether/eval/pooling.py` | Build the shuffled, provenance-stripped judgement pool |
| `bellwether/eval/report.py` | The comparison table Day 8 publishes |
| `bellwether/context/__main__.py` | **Modify** — Day 7's unpaid CLI debt plus `--search` / `--eval` |
| `.gitignore` | **Modify** — `data/*` + `!data/gold/`, see Task 8 |
| `data/gold/day08-retrieval.json` | The committed answer key |

---

### Task 1: The identifier-aware tokenizer

**Files:**
- Create: `bellwether/context/retrieval/__init__.py`, `bellwether/context/retrieval/tokenize.py`
- Test: `tests/bellwether/context/retrieval/__init__.py`, `tests/bellwether/context/retrieval/test_tokenize.py`

**Interfaces — produces:**
- `tokenize(text: str) -> list[str]`
- `STOPWORDS: frozenset[str]`

This file is small and load-bearing. For every raw token it emits **both the original and its sub-parts**. Keeping the original is what lets `budget_micros` score exactly; emitting the parts is what lets a half-remembered `budget micros` still match. Drop either half and hybrid retrieval collapses back into one of the two things it exists to beat.

Duplicates are **not** removed — a whole-token match contributes term frequency for the whole *and* its parts, so an exact identifier hit legitimately scores higher than a partial one. That is the intended behaviour, not an accident.

- [ ] **Step 1: Write the failing test**

```python
# tests/bellwether/context/retrieval/test_tokenize.py
"""The tokenizer that makes hybrid retrieval work — originals kept, parts added."""

from __future__ import annotations

from bellwether.context.retrieval.tokenize import tokenize


def test_snake_case_keeps_the_whole_and_the_parts() -> None:
    tokens = tokenize("budget_micros")
    assert "budget_micros" in tokens
    assert "budget" in tokens
    assert "micros" in tokens


def test_camel_case_splits_and_keeps_the_whole() -> None:
    tokens = tokenize("AdDecisionService")
    assert "addecisionservice" in tokens
    assert "ad" in tokens
    assert "decision" in tokens
    assert "service" in tokens


def test_route_splits_on_slash_and_hyphen() -> None:
    tokens = tokenize("POST /ad-request")
    assert "post" in tokens
    assert "ad-request" in tokens
    assert "ad" in tokens
    assert "request" in tokens


def test_stopwords_are_dropped() -> None:
    assert "the" not in tokenize("the budget")
    assert "budget" in tokenize("the budget")


def test_single_characters_are_dropped() -> None:
    assert tokenize("a b budget") == ["budget"]


def test_is_deterministic() -> None:
    assert tokenize("BudgetMicros enforced") == tokenize("BudgetMicros enforced")


def test_a_leading_underscore_name_keeps_its_whole_form() -> None:
    # The corpus ingests Python source, where `__init__`, `__main__` and private
    # helpers are everywhere. Matching from the first alphanumeric character would
    # silently reduce these to "init" and lose the identifier that was asked for.
    assert "__init__" in tokenize("__init__")
    assert "_keep" in tokenize("_keep")


def test_trailing_sentence_punctuation_is_still_stripped() -> None:
    assert "budget" in tokenize("the budget.")
    assert "budget." not in tokenize("the budget.")


def test_an_underscore_wrapped_stopword_is_still_dropped() -> None:
    # Markdown italics. Allowing `_` to start a token must not let `_the_` in as a
    # term distinct from the stopword `the`.
    assert tokenize("_the_") == []


def test_a_bare_underscore_rule_is_not_a_term() -> None:
    assert tokenize("__") == []


def test_exact_token_contributes_more_frequency_than_its_parts() -> None:
    # The whole plus both parts: three tokens from one identifier. This is what
    # gives an exact-identifier query its edge over a merely-related chunk.
    assert len(tokenize("budget_micros")) == 3


def test_empty_text_is_empty() -> None:
    assert tokenize("") == []
    assert tokenize("   ") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/test_tokenize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.context.retrieval'`

- [ ] **Step 3: Write minimal implementation**

Create `bellwether/context/retrieval/__init__.py` as an empty file, then:

```python
# bellwether/context/retrieval/tokenize.py
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
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
        "how", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to",
        "was", "were", "what", "when", "where", "which", "with",
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
    """A term is worth indexing if it is long enough and not a stopword.

    Membership is tested against the token stripped of its wrapping underscores, so
    markdown emphasis — `_the_`, which this corpus is full of — is filtered like the
    stopword it is rather than slipping in as a distinct term. The cost is that
    operator dunders like `__or__` go with it. That is the right side of the trade:
    nobody searches for `__or__`, and every italicised word in every ADR would
    otherwise become its own index entry.

    The bare-length check also drops a lone `__`, which markdown uses as a rule.
    """
    bare = token.strip("_")
    return len(token) >= MIN_LENGTH and len(bare) >= MIN_LENGTH and bare not in STOPWORDS


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/test_tokenize.py -v`
Expected: PASS, 12 passed

- [ ] **Step 5: Run the gates, each unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
```
Expected: all clean, exit code 0 each

- [ ] **Step 6: Commit**

```bash
git add bellwether/context/retrieval/ tests/bellwether/context/retrieval/
git commit -m "feat: an identifier-aware tokenizer that keeps the whole and the parts"
```

---

### Task 2: BM25 over the chunks

**Files:**
- Create: `bellwether/context/retrieval/bm25.py`
- Test: `tests/bellwether/context/retrieval/test_bm25.py`

**Interfaces:**
- Consumes: `tokenize` (Task 1); `Chunk`, `SearchHit`
- Produces:
  - `BM25Params(k1: float = 1.5, b: float = 0.75)` — frozen dataclass
  - `BM25Index(chunks: Sequence[Chunk], params: BM25Params = BM25Params())`
  - `BM25Index.search(query: str, limit: int = 10, source_types: Sequence[str] | None = None) -> list[SearchHit]`
  - `BM25Index.__len__() -> int`

Hand-written rather than `rank-bm25`: the scoring function is fifteen lines, and a dependency whose source is shorter than its changelog is a liability. Built from the same `list[Chunk]` the vector path embeds — identical inputs, the discipline ADR-0008 imposed on the four engines.

Scores are **not normalised**. Task 3 explains why they must not be.

- [ ] **Step 1: Write the failing test**

```python
# tests/bellwether/context/retrieval/test_bm25.py
"""Lexical retrieval — the half of the system that can find an identifier."""

from __future__ import annotations

from datetime import UTC, datetime

from bellwether.context.chunking.models import Chunk, build_chunk
from bellwether.context.documents import build_document
from bellwether.context.retrieval.bm25 import BM25Index

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _chunk(index: int, text: str, source_type: str = "adr") -> Chunk:
    document = build_document(
        source_path=f"docs/adr/{index:04d}-x.md",
        source_type=source_type,  # type: ignore[arg-type]
        component="docs",
        title=f"ADR-{index:04d}",
        content=text,
        ingested_at=NOW,
    )
    return build_chunk(document, text, "markdown", index, f"ADR-{index:04d}", 1, 2)


def _corpus() -> list[Chunk]:
    return [
        _chunk(1, "The campaign budget_micros field is enforced in the decision service."),
        _chunk(2, "Budgets and spending are tracked daily against a cap."),
        _chunk(3, "Qdrant was chosen over ChromaDB because of named vectors."),
        _chunk(4, "Frequency capping uses Redis with a rolling window."),
    ]


def test_finds_the_exact_identifier_first() -> None:
    index = BM25Index(_corpus())
    hits = index.search("budget_micros", limit=2)
    assert hits[0].chunk_id.endswith("#0001")


def test_returns_search_hits_carrying_provenance() -> None:
    index = BM25Index(_corpus())
    hit = index.search("named vectors", limit=1)[0]
    assert hit.anchor == "ADR-0003"
    assert hit.source_path == "docs/adr/0003-x.md"
    assert hit.source_type == "adr"


def test_scores_are_positive_and_descending() -> None:
    index = BM25Index(_corpus())
    hits = index.search("budget", limit=4)
    scores = [hit.score for hit in hits]
    assert all(score > 0 for score in scores)
    assert scores == sorted(scores, reverse=True)


def test_a_term_in_no_document_returns_nothing() -> None:
    index = BM25Index(_corpus())
    assert index.search("kubernetes") == []


def test_empty_query_returns_nothing() -> None:
    index = BM25Index(_corpus())
    assert index.search("") == []


def test_respects_the_limit() -> None:
    index = BM25Index(_corpus())
    assert len(index.search("budget spending cap decision", limit=2)) == 2


def test_filters_by_source_type() -> None:
    corpus = [*_corpus(), _chunk(5, "budget_micros in code", source_type="code")]
    index = BM25Index(corpus)
    hits = index.search("budget_micros", limit=10, source_types=["code"])
    assert [hit.source_type for hit in hits] == ["code"]


def test_ranking_is_deterministic_across_builds() -> None:
    first = BM25Index(_corpus()).search("budget", limit=4)
    second = BM25Index(_corpus()).search("budget", limit=4)
    assert [hit.chunk_id for hit in first] == [hit.chunk_id for hit in second]


def test_length_normalisation_does_not_favour_long_chunks() -> None:
    short = _chunk(1, "budget_micros")
    padding = " ".join(["irrelevant filler prose"] * 60)
    long = _chunk(2, f"budget_micros {padding}")
    hits = BM25Index([short, long]).search("budget_micros", limit=2)
    assert hits[0].chunk_id.endswith("#0001")


def test_len_reports_the_corpus_size() -> None:
    assert len(BM25Index(_corpus())) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/test_bm25.py -v`
Expected: FAIL — `ImportError: cannot import name 'BM25Index'`

- [ ] **Step 3: Write minimal implementation**

```python
# bellwether/context/retrieval/bm25.py
"""Okapi BM25 over the chunked corpus — fifteen lines of scoring, no dependency.

`rank-bm25` would have done this, but the scoring function below is shorter than
that package's changelog, and a hand-written one can be read, tested and explained
on camera. It also means the identifier-aware tokenizer is not fighting somebody
else's idea of what a word is.

Indexes the *same* `list[Chunk]` the vector path embeds. A lexical index built over
different inputs than the dense one would make the Day 8 comparison meaningless in
the same way four embedding engines on four different chunk sets would have made
Day 7's meaningless.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from bellwether.context.chunking.models import Chunk
from bellwether.context.retrieval.tokenize import tokenize
from bellwether.context.vectors import SearchHit


@dataclass(frozen=True)
class BM25Params:
    """The two constants. Published defaults, not tuned against the eval set."""

    k1: float = 1.5
    b: float = 0.75


class BM25Index:
    """An in-memory inverted index. The corpus is 585 chunks; this is enough."""

    def __init__(self, chunks: Sequence[Chunk], params: BM25Params | None = None) -> None:
        self.params = params or BM25Params()
        self._chunks: list[Chunk] = list(chunks)
        self._frequencies: list[Counter[str]] = []
        self._lengths: list[int] = []
        self._document_frequency: Counter[str] = Counter()

        for chunk in self._chunks:
            terms = tokenize(chunk.text)
            counts = Counter(terms)
            self._frequencies.append(counts)
            self._lengths.append(len(terms))
            self._document_frequency.update(counts.keys())

        total = sum(self._lengths)
        self._average_length = total / len(self._chunks) if self._chunks else 0.0

    def __len__(self) -> int:
        """How many chunks are indexed."""
        return len(self._chunks)

    def _idf(self, term: str) -> float:
        """Inverse document frequency, in the form that cannot go negative."""
        count = self._document_frequency.get(term, 0)
        if count == 0:
            return 0.0
        total = len(self._chunks)
        return math.log(1 + (total - count + 0.5) / (count + 0.5))

    def search(
        self,
        query: str,
        limit: int = 10,
        source_types: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """The best-scoring chunks for `query`, highest first."""
        terms = tokenize(query)
        if not terms or not self._chunks:
            return []

        wanted = set(source_types or ())
        k1 = self.params.k1
        b = self.params.b

        scored: list[tuple[float, str, Chunk]] = []
        for position, chunk in enumerate(self._chunks):
            if wanted and chunk.provenance.source_type not in wanted:
                continue
            counts = self._frequencies[position]
            length = self._lengths[position]
            score = 0.0
            for term in terms:
                frequency = counts.get(term, 0)
                if frequency == 0:
                    continue
                denominator = frequency + k1 * (
                    1 - b + b * (length / self._average_length if self._average_length else 1.0)
                )
                score += self._idf(term) * (frequency * (k1 + 1)) / denominator
            if score > 0:
                scored.append((score, chunk.chunk_id, chunk))

        # Sorted by score, then chunk_id — ties must not depend on dict ordering, or
        # the eval numbers move between runs for no reason anyone can see.
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [_hit(chunk, score) for score, _, chunk in scored[:limit]]


def _hit(chunk: Chunk, score: float) -> SearchHit:
    """The same shape the vector store returns, so fusion never branches on source."""
    provenance = chunk.provenance
    return SearchHit(
        chunk_id=chunk.chunk_id,
        score=score,
        text=chunk.text,
        anchor=provenance.anchor,
        source_path=provenance.source_path,
        source_type=provenance.source_type,
        component=provenance.component,
        title=provenance.title,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/test_bm25.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Run the gates, each unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
python -m uv run pytest
```
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add bellwether/context/retrieval/bm25.py tests/bellwether/context/retrieval/test_bm25.py
git commit -m "feat: hand-written BM25 over the chunked corpus"
```

---

### Task 3: Fusion — RRF, and the alternative it has to beat

**Files:**
- Create: `bellwether/context/retrieval/fusion.py`
- Test: `tests/bellwether/context/retrieval/test_fusion.py`

**Interfaces:**
- Consumes: `SearchHit`
- Produces:
  - `RRF_K: int = 60`
  - `reciprocal_rank_fusion(rankings: Sequence[Sequence[SearchHit]], k: int = RRF_K, limit: int = 10) -> list[SearchHit]`
  - `weighted_fusion(dense: Sequence[SearchHit], lexical: Sequence[SearchHit], alpha: float = 0.5, limit: int = 10) -> list[SearchHit]`

RRF uses only rank position, never the underlying score — cosine similarity lives in `[-1, 1]` and BM25 is unbounded, so any scheme that adds them must first normalise, and **every normalisation is a knob that can be turned until the preferred system wins**. RRF has one constant and it is the published default.

`weighted_fusion` ships as a *measured* alternative rather than a rejected one. It appears in the comparison table as its own row, and if it wins, it wins.

- [ ] **Step 1: Write the failing test**

```python
# tests/bellwether/context/retrieval/test_fusion.py
"""Combining two rankings without inventing a normalisation that picks the winner."""

from __future__ import annotations

from bellwether.context.retrieval.fusion import (
    RRF_K,
    reciprocal_rank_fusion,
    weighted_fusion,
)
from bellwether.context.vectors import SearchHit


def _hit(chunk_id: str, score: float) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=score,
        text=f"text of {chunk_id}",
        anchor=chunk_id,
        source_path=f"docs/{chunk_id}.md",
        source_type="adr",
        component="docs",
        title=chunk_id,
    )


def test_a_chunk_ranked_well_by_both_beats_one_ranked_well_by_either() -> None:
    dense = [_hit("a", 0.9), _hit("b", 0.8)]
    lexical = [_hit("b", 12.0), _hit("c", 9.0)]
    fused = reciprocal_rank_fusion([dense, lexical], limit=3)
    assert fused[0].chunk_id == "b"


def test_fused_score_is_the_rrf_sum_not_the_original() -> None:
    dense = [_hit("a", 0.9)]
    lexical = [_hit("a", 250.0)]
    fused = reciprocal_rank_fusion([dense, lexical], limit=1)
    assert fused[0].score == 2 / (RRF_K + 1)


def test_ignores_the_magnitude_of_the_input_scores() -> None:
    # The whole point: BM25 at 250.0 must not outvote cosine at 0.9 by being bigger.
    small = reciprocal_rank_fusion([[_hit("a", 0.01)], [_hit("b", 0.02)]], limit=2)
    huge = reciprocal_rank_fusion([[_hit("a", 1000.0)], [_hit("b", 2000.0)]], limit=2)
    assert [hit.chunk_id for hit in small] == [hit.chunk_id for hit in huge]


def test_preserves_provenance_from_the_first_list_that_saw_the_chunk() -> None:
    fused = reciprocal_rank_fusion([[_hit("a", 0.9)], [_hit("a", 3.0)]], limit=1)
    assert fused[0].anchor == "a"
    assert fused[0].source_path == "docs/a.md"


def test_empty_rankings_fuse_to_nothing() -> None:
    assert reciprocal_rank_fusion([], limit=5) == []
    assert reciprocal_rank_fusion([[], []], limit=5) == []


def test_one_ranking_fuses_to_itself_in_order() -> None:
    fused = reciprocal_rank_fusion([[_hit("a", 0.9), _hit("b", 0.5)]], limit=2)
    assert [hit.chunk_id for hit in fused] == ["a", "b"]


def test_respects_the_limit() -> None:
    dense = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
    assert len(reciprocal_rank_fusion([dense], limit=2)) == 2


def test_ties_break_on_chunk_id_not_dict_order() -> None:
    fused = reciprocal_rank_fusion([[_hit("z", 0.5)], [_hit("a", 0.5)]], limit=2)
    assert [hit.chunk_id for hit in fused] == ["a", "z"]


def test_weighted_fusion_at_alpha_one_is_dense_only() -> None:
    dense = [_hit("a", 0.9), _hit("b", 0.1)]
    lexical = [_hit("c", 20.0)]
    fused = weighted_fusion(dense, lexical, alpha=1.0, limit=1)
    assert fused[0].chunk_id == "a"


def test_weighted_fusion_at_alpha_zero_is_lexical_only() -> None:
    dense = [_hit("a", 0.9)]
    lexical = [_hit("c", 20.0), _hit("d", 1.0)]
    fused = weighted_fusion(dense, lexical, alpha=0.0, limit=1)
    assert fused[0].chunk_id == "c"


def test_weighted_fusion_survives_a_single_element_list() -> None:
    # min == max, so the normaliser divides by zero unless it is guarded.
    fused = weighted_fusion([_hit("a", 0.9)], [_hit("b", 5.0)], alpha=0.5, limit=2)
    assert {hit.chunk_id for hit in fused} == {"a", "b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/test_fusion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.context.retrieval.fusion'`

- [ ] **Step 3: Write minimal implementation**

```python
# bellwether/context/retrieval/fusion.py
"""Combine a dense ranking and a lexical one without picking the winner in advance.

Cosine similarity lives in [-1, 1]. BM25 is unbounded and routinely reaches 20.
Adding them requires normalising them first, and every normalisation scheme is a
tuning knob — one that can be turned, consciously or not, until the system you were
hoping to promote comes out ahead. On a day whose entire deliverable is an honest
comparison, that is not a knob worth having.

Reciprocal Rank Fusion uses rank position only. It never sees the scores. It has one
constant, k=60, which is the value from the original paper and is not tuned here.

`weighted_fusion` is the normalising alternative, kept so the choice is measured
rather than asserted. It gets its own row in the comparison table.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from bellwether.context.vectors import SearchHit

# From Cormack et al. 2009. Published default, deliberately not tuned against the
# gold set — a fusion constant fitted on the eval set is the eval set's constant.
RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[SearchHit]],
    k: int = RRF_K,
    limit: int = 10,
) -> list[SearchHit]:
    """Fuse rankings on rank position alone: sum of 1 / (k + rank)."""
    scores: dict[str, float] = {}
    seen: dict[str, SearchHit] = {}

    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1 / (k + rank)
            seen.setdefault(hit.chunk_id, hit)

    return _top(scores, seen, limit)


def weighted_fusion(
    dense: Sequence[SearchHit],
    lexical: Sequence[SearchHit],
    alpha: float = 0.5,
    limit: int = 10,
) -> list[SearchHit]:
    """Min-max normalise each side, then `alpha * dense + (1 - alpha) * lexical`."""
    scores: dict[str, float] = {}
    seen: dict[str, SearchHit] = {}

    for ranking, weight in ((dense, alpha), (lexical, 1 - alpha)):
        for chunk_id, normalized in _normalize(ranking).items():
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight * normalized
        for hit in ranking:
            seen.setdefault(hit.chunk_id, hit)

    return _top(scores, seen, limit)


def _normalize(ranking: Sequence[SearchHit]) -> dict[str, float]:
    """Min-max to [0, 1]. A single hit, or an all-equal ranking, normalises to 1.0."""
    if not ranking:
        return {}
    values = [hit.score for hit in ranking]
    lowest = min(values)
    highest = max(values)
    if highest == lowest:
        return {hit.chunk_id: 1.0 for hit in ranking}
    span = highest - lowest
    return {hit.chunk_id: (hit.score - lowest) / span for hit in ranking}


def _top(scores: dict[str, float], seen: dict[str, SearchHit], limit: int) -> list[SearchHit]:
    """Highest score first, ties broken on chunk_id so runs are reproducible."""
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [dataclasses.replace(seen[chunk_id], score=score) for chunk_id, score in ordered[:limit]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/test_fusion.py -v`
Expected: PASS, 11 passed

- [ ] **Step 5: Run the gates, each unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
python -m uv run pytest
```

- [ ] **Step 6: Commit**

```bash
git add bellwether/context/retrieval/fusion.py tests/bellwether/context/retrieval/test_fusion.py
git commit -m "feat: reciprocal rank fusion, with weighted fusion as the measured alternative"
```

---

### Task 4: The reranker protocol and the free reranker

**Files:**
- Create: `bellwether/context/retrieval/rerank/__init__.py`, `rerank/base.py`, `rerank/heuristic.py`
- Test: `tests/bellwether/context/retrieval/rerank/__init__.py`, `rerank/test_heuristic.py`

**Interfaces:**
- Consumes: `SearchHit`, `UsageRecord`, `tokenize`
- Produces:
  - `RerankerSpec(name: str, label: str, hosted: bool, notes: str)` — frozen dataclass
  - `RerankResult(hits: list[SearchHit], usage: UsageRecord | None)` — frozen dataclass
  - `RerankError(RuntimeError)`
  - `Reranker` Protocol: `spec`, `available() -> tuple[bool, str]`, `rerank(query, hits, limit) -> RerankResult`
  - `HeuristicReranker(weights: HeuristicWeights | None = None)`
  - `HeuristicWeights(identifier: float = 0.5, anchor: float = 0.3, source_prior: float = 0.15, length_penalty: float = 0.1)`

Deliberately identical in shape to Day 7's `Embedder` — `spec` / `available()` / one verb — because the lesson generalises: *the engine is a parameter, not a commitment.*

`HeuristicReranker` is a **real reranker with a defensible feature set, not a stub**. That is what lets the test suite assert rerank behaviour with no model and no network.

- [ ] **Step 1: Write the failing test**

```python
# tests/bellwether/context/retrieval/rerank/test_heuristic.py
"""The free reranker — real features, no network, and the CI default."""

from __future__ import annotations

from bellwether.context.retrieval.rerank import HeuristicReranker
from bellwether.context.vectors import SearchHit


def _hit(
    chunk_id: str,
    score: float,
    text: str = "some text",
    anchor: str | None = None,
    source_type: str = "devlog",
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=score,
        text=text,
        anchor=anchor,
        source_path=f"docs/{chunk_id}.md",
        source_type=source_type,
        component="docs",
        title=chunk_id,
    )


def test_is_always_available_and_says_so() -> None:
    available, reason = HeuristicReranker().available()
    assert available is True
    assert reason


def test_costs_nothing_and_reports_no_usage() -> None:
    result = HeuristicReranker().rerank("budget", [_hit("a", 1.0)], limit=1)
    assert result.usage is None


def test_promotes_an_exact_identifier_match_over_a_higher_ranked_near_miss() -> None:
    hits = [
        _hit("a", 0.9, text="budgets are discussed at length here"),
        _hit("b", 0.5, text="the budget_micros field is validated on write"),
    ]
    result = HeuristicReranker().rerank("budget_micros", hits, limit=2)
    assert result.hits[0].chunk_id == "b"


def test_promotes_an_anchor_match() -> None:
    hits = [
        _hit("a", 0.9, text="unrelated prose", anchor="Redis keys"),
        _hit("b", 0.6, text="unrelated prose", anchor="Frequency capping"),
    ]
    result = HeuristicReranker().rerank("frequency capping", hits, limit=2)
    assert result.hits[0].chunk_id == "b"


def test_a_why_question_prefers_an_adr_over_a_devlog() -> None:
    hits = [
        _hit("a", 0.8, text="we picked qdrant", source_type="devlog"),
        _hit("b", 0.8, text="we picked qdrant", source_type="adr"),
    ]
    result = HeuristicReranker().rerank("why did we pick qdrant", hits, limit=2)
    assert result.hits[0].chunk_id == "b"


def test_a_very_long_chunk_is_penalised_against_a_focused_one() -> None:
    focused = _hit("a", 0.7, text="budget_micros is enforced here")
    sprawling = _hit("b", 0.7, text="budget_micros " + ("filler " * 400))
    result = HeuristicReranker().rerank("budget_micros", [sprawling, focused], limit=2)
    assert result.hits[0].chunk_id == "a"


def test_an_ordinary_long_word_is_not_treated_as_an_identifier() -> None:
    # "observability" is thirteen characters of plain English. If length alone
    # qualified it, the largest boost in the table would fire on prose, inflating
    # the free baseline that Day 8 measures the LLM reranker against.
    hits = [
        _hit("a", 0.9, text="unrelated prose about caching"),
        _hit("b", 0.5, text="observability is covered in the runbook"),
    ]
    result = HeuristicReranker().rerank("observability", hits, limit=2)
    assert result.hits[0].chunk_id == "a"


def test_preserves_the_fused_order_when_no_feature_fires() -> None:
    hits = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
    result = HeuristicReranker().rerank("kubernetes helm chart", hits, limit=3)
    assert [hit.chunk_id for hit in result.hits] == ["a", "b", "c"]


def test_respects_the_limit() -> None:
    hits = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
    assert len(HeuristicReranker().rerank("anything", hits, limit=2).hits) == 2


def test_empty_input_reranks_to_empty() -> None:
    assert HeuristicReranker().rerank("anything", [], limit=5).hits == []


def test_is_deterministic() -> None:
    hits = [_hit("a", 0.5), _hit("b", 0.5), _hit("c", 0.5)]
    first = HeuristicReranker().rerank("budget", hits, limit=3).hits
    second = HeuristicReranker().rerank("budget", hits, limit=3).hits
    assert [hit.chunk_id for hit in first] == [hit.chunk_id for hit in second]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/rerank/test_heuristic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.context.retrieval.rerank'`

- [ ] **Step 3: Write `rerank/base.py`**

```python
# bellwether/context/retrieval/rerank/base.py
"""One protocol, two rerankers, and a cost recorded when there is one.

Shaped deliberately like Day 7's `Embedder` — `spec`, `available()`, one verb —
because the lesson generalises: the engine is a parameter, not a commitment. A
reranker that cannot run says why, in words a human can act on, and the caller
falls back to the fused order rather than to nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from bellwether.context.embedders.base import UsageRecord
from bellwether.context.vectors import SearchHit


class RerankError(RuntimeError):
    """A reranker could not run. Always names the backend that failed."""


@dataclass(frozen=True)
class RerankerSpec:
    """What a reranker is, and what it costs — its row in the comparison."""

    name: str
    label: str
    hosted: bool
    notes: str


@dataclass(frozen=True)
class RerankResult:
    """A reordered ranking, and the bill for producing it if there was one."""

    hits: list[SearchHit]
    usage: UsageRecord | None


class Reranker(Protocol):
    """Everything the search service needs from a reranker."""

    @property
    def spec(self) -> RerankerSpec:
        """What this reranker is and what it costs."""
        ...

    def available(self) -> tuple[bool, str]:
        """Whether it can run, and if not, a reason a human can act on."""
        ...

    def rerank(self, query: str, hits: Sequence[SearchHit], limit: int) -> RerankResult:
        """Reorder `hits` by relevance to `query`, returning the best `limit`."""
        ...
```

- [ ] **Step 4: Write `rerank/heuristic.py`**

```python
# bellwether/context/retrieval/rerank/heuristic.py
"""A reranker with no model, no network and no bill — and real features.

This is not a stub standing in for the LLM reranker. It is a defensible baseline
that the LLM has to beat, built from four signals that a retrieval engineer would
recognise: does the chunk contain the identifier that was asked for, does its anchor
name the thing, is its document type the kind that answers this kind of question,
and is the match incidental to a wall of text.

Because it is real, the test suite can assert rerank *behaviour* without a model —
which is what keeps the whole suite hermetic.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass

from bellwether.context.retrieval.rerank.base import RerankerSpec, RerankResult
from bellwether.context.retrieval.tokenize import tokenize
from bellwether.context.vectors import SearchHit

# A question starting with one of these is asking for a decision and its reasoning,
# which is what an ADR is and what a devlog only incidentally contains.
WHY_WORDS = frozenset({"why", "rationale", "reason", "decide", "decided", "chose", "chosen"})

WHY_PREFERRED = frozenset({"adr", "spec", "standards"})

# Long enough that a single term match says little about what the chunk is about.
SPRAWL_CHARS = 1200

# How fast the incoming rank decays into the base score.
#
# The base must be scale-free. Using `hit.score` directly would make the boosts
# meaningless or overwhelming depending on which mode fed us — RRF scores sit near
# 0.016, BM25 scores reach 20 — which is the exact scale-mixing problem ADR-0009
# rejects. So rank position is the only input.
#
# 0.05 sets the exchange rate: one rank position is worth about 0.05, so a single
# exact-identifier match (0.5) is worth roughly ten places. That is the intended
# strength — an identifier hit should climb past near-misses, not merely nudge.
RANK_STEP = 0.05


@dataclass(frozen=True)
class HeuristicWeights:
    """How much each signal moves a chunk. Published, not fitted to the gold set."""

    identifier: float = 0.5
    anchor: float = 0.3
    source_prior: float = 0.15
    length_penalty: float = 0.1


class HeuristicReranker:
    """Free, deterministic, offline. The CI default and the LLM's baseline."""

    def __init__(self, weights: HeuristicWeights | None = None) -> None:
        self.weights = weights or HeuristicWeights()

    @property
    def spec(self) -> RerankerSpec:
        """What this reranker is and what it costs."""
        return RerankerSpec(
            name="heuristic",
            label="Heuristic",
            hosted=False,
            notes="identifier, anchor, source-type prior, length penalty",
        )

    def available(self) -> tuple[bool, str]:
        """Always available — no key, no package, no network."""
        return True, "no dependencies"

    def rerank(self, query: str, hits: Sequence[SearchHit], limit: int) -> RerankResult:
        """Reorder `hits` by the four signals, preserving fused order on ties."""
        if not hits:
            return RerankResult(hits=[], usage=None)

        terms = tokenize(query)
        # A separator is the only honest signal that a term is a name rather than a
        # word. A length threshold looks tempting — it would catch `addecisionservice`,
        # whose capitals tokenization has already discarded — but it also hands the
        # largest boost in the table to "frequency", "kubernetes" and "observability".
        # That would inflate the free baseline on ordinary prose, and this day exists
        # to compare that baseline against an LLM honestly. camelCase wholes still
        # reach the top via BM25's IDF; they just do not collect a bonus here.
        identifiers = {term for term in terms if any(mark in term for mark in "_-./")}
        wants_why = bool(WHY_WORDS & set(terms))

        scored: list[tuple[float, int, SearchHit]] = []
        for position, hit in enumerate(hits):
            # Base keeps the incoming order meaningful: a reranker that discards the
            # retriever's opinion entirely is a retriever, not a reranker. Decays
            # with rank rather than with the incoming score, so the boosts below
            # mean the same thing whichever mode produced these hits — and stays
            # positive and monotonic however deep the candidate list runs.
            score = 1.0 / (1.0 + position * RANK_STEP)
            score += self._boost(hit, terms, identifiers, wants_why)
            scored.append((score, position, hit))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return RerankResult(
            hits=[dataclasses.replace(hit, score=score) for score, _, hit in scored[:limit]],
            usage=None,
        )

    def _boost(
        self,
        hit: SearchHit,
        terms: Sequence[str],
        identifiers: set[str],
        wants_why: bool,
    ) -> float:
        """How far the four signals move this one chunk."""
        weights = self.weights
        text = hit.text.lower()
        boost = 0.0

        if identifiers and any(identifier in text for identifier in identifiers):
            boost += weights.identifier

        anchor = (hit.anchor or "").lower()
        if anchor and any(term in anchor for term in terms):
            boost += weights.anchor

        if wants_why and hit.source_type in WHY_PREFERRED:
            boost += weights.source_prior

        if len(hit.text) > SPRAWL_CHARS:
            boost -= weights.length_penalty

        return boost
```

- [ ] **Step 5: Write `rerank/__init__.py`**

```python
# bellwether/context/retrieval/rerank/__init__.py
"""The reranker registry — everything the search service can slot in."""

from __future__ import annotations

from bellwether.context.retrieval.rerank.base import (
    Reranker,
    RerankError,
    RerankerSpec,
    RerankResult,
)
from bellwether.context.retrieval.rerank.heuristic import HeuristicReranker, HeuristicWeights

__all__ = [
    "HeuristicReranker",
    "HeuristicWeights",
    "RerankError",
    "RerankResult",
    "Reranker",
    "RerankerSpec",
]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/rerank/test_heuristic.py -v`
Expected: PASS, 11 passed

- [ ] **Step 7: Run the gates, each unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
python -m uv run pytest
```

- [ ] **Step 8: Commit**

```bash
git add bellwether/context/retrieval/rerank/ tests/bellwether/context/retrieval/rerank/
git commit -m "feat: a reranker protocol, and a free deterministic reranker behind it"
```

---

### Task 5: The LLM client — spec §4.6's first piece

**Files:**
- Create: `bellwether/llm/__init__.py`, `llm/base.py`, `llm/gemini.py`, `llm/claude.py`
- Test: `tests/bellwether/llm/__init__.py`, `tests/bellwether/llm/test_llm.py`

**Interfaces:**
- Consumes: `UsageRecord`, `cost_for`, `HttpPost` (all from `bellwether.context.embedders.base` — one cost vocabulary, not two)
- Produces:
  - `ModelSpec(name, label, model_id, hosted, cost_per_million_input, cost_per_million_output, notes)`
  - `LLMResponse(text: str, data: object | None, usage: UsageRecord)`
  - `LLMError(RuntimeError)`
  - `LLMClient` Protocol: `spec`, `available() -> tuple[bool, str]`, `complete(prompt: str, schema: dict[str, Any], max_tokens: int = 2048) -> LLMResponse`
  - `GeminiClient(api_key=None, transport=None, model_id=None)`
  - `ClaudeClient(api_key=None, transport=None, model_id=None)`
  - `REGISTRY: dict[str, Callable[[], LLMClient]]`, `get_client(name) -> LLMClient`, `DEFAULT_CLIENT = "gemini"`

**Two things to be explicit about.**

**Gemini ships wired.** The `GEMINI_API_KEY` already works and is billed — Day 7's embedding run spent $0.0223 through it — so Day 8 needs no new credential to produce a real number.

**Claude is written but has never run.** Spec §4.6 names Claude (Haiku-class for dev) and Levels 2–4 all assume it, so the backend is implemented and hermetically tested here rather than left as a stub — when the key arrives it works. But it has **never been exercised against the live API**, and the devlog must say so. Note `claude-haiku-4-5` is a pre-4.6 model: it takes `thinking: {"type": "enabled", "budget_tokens": N}` and **`output_config.effort` returns an error on it**, so neither is sent. Model ids are environment-overridable, never hardcoded in logic (spec §4.6).

> **Before the real run in Task 10**, confirm the Gemini generation model id is current — the default below is a starting point, not a verified fact, and a stale model name in a portfolio repo is exactly the signal spec §4.6 warns about. Override with `BELLWETHER_GEMINI_MODEL` if it has moved.

- [ ] **Step 1: Write the failing test**

```python
# tests/bellwether/llm/test_llm.py
"""One protocol, two backends, and no test that opens a socket."""

from __future__ import annotations

import json
from typing import Any

import pytest

from bellwether.llm import REGISTRY, ClaudeClient, GeminiClient, LLMError, get_client

SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"chunk_id": {"type": "string"}, "relevance": {"type": "integer"}},
        "required": ["chunk_id", "relevance"],
    },
}


class FakePost:
    """Answers one scripted response, and records what it was sent."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, payload, headers))
        return self.status, self.body


def _gemini_body(text: str, prompt_tokens: int = 100, output_tokens: int = 20) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": output_tokens,
        },
    }


def _claude_body(text: str, input_tokens: int = 100, output_tokens: int = 20) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def test_gemini_is_unavailable_without_a_key_and_says_which_one() -> None:
    available, reason = GeminiClient(api_key="").available()
    assert available is False
    assert "GEMINI_API_KEY" in reason


def test_claude_is_unavailable_without_a_key_and_says_which_one() -> None:
    available, reason = ClaudeClient(api_key="").available()
    assert available is False
    assert "ANTHROPIC_API_KEY" in reason


def test_gemini_parses_structured_output() -> None:
    payload = json.dumps([{"chunk_id": "a#0001", "relevance": 2}])
    client = GeminiClient(api_key="k", transport=FakePost(200, _gemini_body(payload)))
    response = client.complete("rank these", SCHEMA)
    assert response.data == [{"chunk_id": "a#0001", "relevance": 2}]


def test_gemini_sends_the_schema_and_asks_for_json() -> None:
    post = FakePost(200, _gemini_body("[]"))
    GeminiClient(api_key="k", transport=post).complete("rank these", SCHEMA)
    _, payload, _ = post.calls[0]
    config = payload["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == SCHEMA


def test_gemini_never_puts_the_key_in_the_payload() -> None:
    post = FakePost(200, _gemini_body("[]"))
    GeminiClient(api_key="sekrit", transport=post).complete("rank these", SCHEMA)
    _, payload, _ = post.calls[0]
    assert "sekrit" not in json.dumps(payload)


def test_gemini_computes_cost_from_the_reported_tokens() -> None:
    post = FakePost(200, _gemini_body("[]", prompt_tokens=1_000_000, output_tokens=0))
    client = GeminiClient(api_key="k", transport=post)
    response = client.complete("rank these", SCHEMA)
    assert response.usage.tokens == 1_000_000
    assert response.usage.cost_usd == pytest.approx(client.spec.cost_per_million_input)


def test_gemini_maps_a_non_200_to_a_typed_error_naming_the_backend() -> None:
    client = GeminiClient(api_key="k", transport=FakePost(429, {"error": {"message": "slow down"}}))
    with pytest.raises(LLMError, match="gemini"):
        client.complete("rank these", SCHEMA)


@pytest.mark.parametrize("error_body", [{"error": None}, {"error": "gateway timeout"}, {}])
def test_a_non_dict_error_body_still_raises_a_typed_error(error_body: dict[str, Any]) -> None:
    # Proxies and gateways return these shapes on 429 and 5xx. Reaching for
    # .get("message") on them is an AttributeError, not an LLMError.
    client = GeminiClient(api_key="k", transport=FakePost(503, error_body))
    with pytest.raises(LLMError, match="gemini"):
        client.complete("rank these", SCHEMA)


@pytest.mark.parametrize("error_body", [{"error": None}, {"error": "gateway timeout"}, {}])
def test_claude_also_survives_a_non_dict_error_body(error_body: dict[str, Any]) -> None:
    client = ClaudeClient(api_key="k", transport=FakePost(503, error_body))
    with pytest.raises(LLMError, match="claude"):
        client.complete("rank these", SCHEMA)


def test_malformed_json_raises_rather_than_returning_junk() -> None:
    client = GeminiClient(api_key="k", transport=FakePost(200, _gemini_body("not json at all")))
    with pytest.raises(LLMError, match="gemini"):
        client.complete("rank these", SCHEMA)


def test_claude_parses_structured_output() -> None:
    payload = json.dumps([{"chunk_id": "a#0001", "relevance": 1}])
    client = ClaudeClient(api_key="k", transport=FakePost(200, _claude_body(payload)))
    response = client.complete("rank these", SCHEMA)
    assert response.data == [{"chunk_id": "a#0001", "relevance": 1}]


def test_claude_sends_the_key_as_a_header_and_never_in_the_body() -> None:
    post = FakePost(200, _claude_body("[]"))
    ClaudeClient(api_key="sekrit", transport=post).complete("rank these", SCHEMA)
    _, payload, headers = post.calls[0]
    assert headers["x-api-key"] == "sekrit"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "sekrit" not in json.dumps(payload)


def test_claude_does_not_send_effort_which_haiku_rejects() -> None:
    post = FakePost(200, _claude_body("[]"))
    ClaudeClient(api_key="k", transport=post).complete("rank these", SCHEMA)
    _, payload, _ = post.calls[0]
    assert "output_config" not in payload


def test_claude_bills_input_and_output_at_different_rates() -> None:
    post = FakePost(200, _claude_body("[]", input_tokens=1_000_000, output_tokens=1_000_000))
    client = ClaudeClient(api_key="k", transport=post)
    response = client.complete("rank these", SCHEMA)
    expected = client.spec.cost_per_million_input + client.spec.cost_per_million_output
    assert response.usage.cost_usd == pytest.approx(expected)


def test_the_registry_lists_both_backends() -> None:
    assert set(REGISTRY) == {"gemini", "claude"}


def test_get_client_rejects_an_unknown_name() -> None:
    with pytest.raises(KeyError):
        get_client("gpt")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m uv run pytest tests/bellwether/llm/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.llm'`

- [ ] **Step 3: Write `llm/base.py`**

```python
# bellwether/llm/base.py
"""The provider seam. Deliberately thin — a protocol, a transport, and a bill.

Spec section 4.6 calls for an LLM abstraction with per-call token and cost tracking
and no model names hardcoded in logic. This is that, and no more: not a framework,
not a chain, not an agent runtime. Those arrive at Level 4 and will sit on top of
this rather than replacing it.

Cost tracking reuses Day 7's `UsageRecord` verbatim rather than defining a parallel
vocabulary, so one report can total embedding spend and generation spend together.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from bellwether.context.embedders.base import HttpPost, UsageRecord

DEFAULT_MAX_TOKENS = 2048


class LLMError(RuntimeError):
    """A backend could not complete. Always names the backend that failed."""


@dataclass(frozen=True)
class ModelSpec:
    """What a backend is and what it charges, input and output priced apart."""

    name: str
    label: str
    model_id: str
    hosted: bool
    cost_per_million_input: float
    cost_per_million_output: float
    notes: str


@dataclass(frozen=True)
class LLMResponse:
    """The text, the parsed structure if a schema was given, and the bill."""

    text: str
    data: object | None
    usage: UsageRecord


class LLMClient(Protocol):
    """Everything the rerankers and, later, the agents need from a provider."""

    @property
    def spec(self) -> ModelSpec:
        """What this backend is and what it costs."""
        ...

    def available(self) -> tuple[bool, str]:
        """Whether it can run, and if not, a reason a human can act on."""
        ...

    def complete(
        self, prompt: str, schema: dict[str, Any], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> LLMResponse:
        """Answer `prompt`, constrained to `schema`, reporting what it consumed."""
        ...


def env_model(variable: str, fallback: str) -> str:
    """A model id from the environment, or the published default.

    Never hardcode a model id in logic. Two-year-stale model names in a portfolio
    repo signal copy-paste planning, and providers rename faster than a 30-day build
    can keep up with.
    """
    return os.environ.get(variable) or fallback


def parse_structured(backend: str, text: str) -> object:
    """Decode a structured response, or fail loudly naming the backend."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise LLMError(f"{backend} returned text that is not JSON: {text[:200]}") from error


def httpx_post(timeout_seconds: float = 60.0) -> HttpPost:
    """The real transport. Imported lazily so tests never construct a client."""
    import httpx

    def post(
        url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout_seconds)
        try:
            body: dict[str, Any] = response.json()
        except ValueError:
            body = {"error": {"message": response.text[:500]}}
        return response.status_code, body

    return post
```

- [ ] **Step 4: Write `llm/gemini.py`**

```python
# bellwether/llm/gemini.py
"""Gemini, over plain HTTP with an injected transport.

Wired first because its key already works and is billed — Day 7's embedding run
spent $0.0223 through it — so Day 8 can produce a real reranking number without
waiting on a new billing relationship.
"""

from __future__ import annotations

import os
import time
from typing import Any

from bellwether.context.embedders.base import HttpPost, UsageRecord, cost_for
from bellwether.llm.base import (
    DEFAULT_MAX_TOKENS,
    LLMError,
    LLMResponse,
    ModelSpec,
    env_model,
    httpx_post,
    parse_structured,
)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

# A starting point, not a verified fact. Override with BELLWETHER_GEMINI_MODEL and
# confirm against the provider's current model list before publishing any number.
DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiClient:
    """One completion call, a schema, and a cost read from the response."""

    def __init__(
        self,
        api_key: str | None = None,
        transport: HttpPost | None = None,
        model_id: str | None = None,
    ) -> None:
        self._api_key = os.environ.get("GEMINI_API_KEY", "") if api_key is None else api_key
        self._transport = transport
        self._model_id = model_id or env_model("BELLWETHER_GEMINI_MODEL", DEFAULT_MODEL)

    @property
    def spec(self) -> ModelSpec:
        """What this backend is and what it charges."""
        return ModelSpec(
            name="gemini",
            label="Gemini",
            model_id=self._model_id,
            hosted=True,
            cost_per_million_input=0.30,
            cost_per_million_output=2.50,
            notes="wired first — the key already works and is billed",
        )

    def available(self) -> tuple[bool, str]:
        """Whether the key is present, and which one is missing if not."""
        if not self._api_key:
            return False, "no GEMINI_API_KEY"
        return True, f"ready ({self._model_id})"

    def complete(
        self, prompt: str, schema: dict[str, Any], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> LLMResponse:
        """Answer `prompt` as JSON matching `schema`, and report the bill."""
        available, reason = self.available()
        if not available:
            raise LLMError(f"gemini unavailable: {reason}")

        post = self._transport or httpx_post()
        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "maxOutputTokens": max_tokens,
            },
        }
        # The key rides in the header, never the body — the body is what gets logged.
        headers = {"x-goog-api-key": self._api_key, "content-type": "application/json"}
        url = f"{ENDPOINT}/{self._model_id}:generateContent"

        started = time.perf_counter()
        status, body = post(url, payload, headers)
        elapsed_ms = (time.perf_counter() - started) * 1000

        if status != 200:
            # `body.get("error", {})` only defaults when the key is absent. A present
            # `"error": null` or `"error": "gateway timeout"` — both routine from
            # proxies on a 429 or 5xx — would then hit .get() on None or a str and
            # raise AttributeError, which is the one thing this branch exists to
            # prevent. Day 7's embedders/hosted.py `_require` already guards this way.
            error = body.get("error")
            message = error.get("message", error) if isinstance(error, dict) else (error or body)
            raise LLMError(f"gemini returned {status}: {message}")

        text = _first_text(body)
        usage = body.get("usageMetadata", {})
        input_tokens = int(usage.get("promptTokenCount") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or 0)
        spec = self.spec

        return LLMResponse(
            text=text,
            data=parse_structured("gemini", text),
            usage=UsageRecord(
                engine="gemini",
                texts=1,
                tokens=input_tokens + output_tokens,
                cost_usd=cost_for(input_tokens, spec.cost_per_million_input)
                + cost_for(output_tokens, spec.cost_per_million_output),
                latency_ms=elapsed_ms,
            ),
        )


def _first_text(body: dict[str, Any]) -> str:
    """The first text part of the first candidate, or a typed failure."""
    candidates = body.get("candidates") or []
    if not candidates:
        raise LLMError(f"gemini returned no candidates: {body}")
    parts = candidates[0].get("content", {}).get("parts") or []
    if not parts:
        raise LLMError(f"gemini returned no parts: {body}")
    return str(parts[0].get("text", ""))
```

- [ ] **Step 5: Write `llm/claude.py`**

```python
# bellwether/llm/claude.py
"""Claude — written, hermetically tested, and never yet run against the live API.

Spec section 4.6 names Claude API (Haiku-class for dev work) as the platform's LLM,
and every agent from Level 2 onward assumes it. Shipping only Gemini would leave the
whole AI layer on a provider Level 2 does not use, so the backend is implemented
here rather than stubbed: when the key arrives, it works.

What is owed is the credential, not the code. Until ANTHROPIC_API_KEY exists this
class has never made a real request, and the devlog says so rather than implying a
verification that did not happen.

`claude-haiku-4-5` is a pre-4.6 model: `output_config.effort` errors on it, so it is
not sent. Structured output is requested through a tool with an input schema, which
is the shape this model supports.
"""

from __future__ import annotations

import os
import time
from typing import Any

from bellwether.context.embedders.base import HttpPost, UsageRecord, cost_for
from bellwether.llm.base import (
    DEFAULT_MAX_TOKENS,
    LLMError,
    LLMResponse,
    ModelSpec,
    env_model,
    httpx_post,
    parse_structured,
)

ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5"
TOOL_NAME = "emit_ranking"


class ClaudeClient:
    """One messages call, structured output through a tool, and a per-call bill."""

    def __init__(
        self,
        api_key: str | None = None,
        transport: HttpPost | None = None,
        model_id: str | None = None,
    ) -> None:
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "") if api_key is None else api_key
        self._transport = transport
        self._model_id = model_id or env_model("BELLWETHER_CLAUDE_MODEL", DEFAULT_MODEL)

    @property
    def spec(self) -> ModelSpec:
        """What this backend is and what it charges."""
        return ModelSpec(
            name="claude",
            label="Claude Haiku",
            model_id=self._model_id,
            hosted=True,
            cost_per_million_input=1.00,
            cost_per_million_output=5.00,
            notes="owed since Day 8 — code written, never run against the live API",
        )

    def available(self) -> tuple[bool, str]:
        """Whether the key is present, and which one is missing if not."""
        if not self._api_key:
            return False, "no ANTHROPIC_API_KEY"
        return True, f"ready ({self._model_id})"

    def complete(
        self, prompt: str, schema: dict[str, Any], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> LLMResponse:
        """Answer `prompt` as JSON matching `schema`, and report the bill."""
        available, reason = self.available()
        if not available:
            raise LLMError(f"claude unavailable: {reason}")

        post = self._transport or httpx_post()
        payload: dict[str, Any] = {
            "model": self._model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": "Emit the ranking as structured data.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"ranking": schema},
                        "required": ["ranking"],
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

        started = time.perf_counter()
        status, body = post(ENDPOINT, payload, headers)
        elapsed_ms = (time.perf_counter() - started) * 1000

        if status != 200:
            # `body.get("error", {})` only defaults when the key is absent. A present
            # `"error": null` or `"error": "gateway timeout"` — both routine from
            # proxies on a 429 or 5xx — would then hit .get() on None or a str and
            # raise AttributeError, which is the one thing this branch exists to
            # prevent. Day 7's embedders/hosted.py `_require` already guards this way.
            error = body.get("error")
            message = error.get("message", error) if isinstance(error, dict) else (error or body)
            raise LLMError(f"claude returned {status}: {message}")

        text, data = _extract(body)
        usage = body.get("usage", {})
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        spec = self.spec

        return LLMResponse(
            text=text,
            data=data,
            usage=UsageRecord(
                engine="claude",
                texts=1,
                tokens=input_tokens + output_tokens,
                cost_usd=cost_for(input_tokens, spec.cost_per_million_input)
                + cost_for(output_tokens, spec.cost_per_million_output),
                latency_ms=elapsed_ms,
            ),
        )


def _extract(body: dict[str, Any]) -> tuple[str, object]:
    """The tool input if the model called the tool, else the text block."""
    blocks = body.get("content") or []
    if not blocks:
        raise LLMError(f"claude returned no content: {body}")

    for block in blocks:
        if block.get("type") == "tool_use":
            payload = block.get("input", {})
            return str(payload), payload.get("ranking", payload)

    text = str(blocks[0].get("text", ""))
    return text, parse_structured("claude", text)
```

- [ ] **Step 6: Write `llm/__init__.py`**

```python
# bellwether/llm/__init__.py
"""The provider registry — spec section 4.6's LLM abstraction, at its smallest."""

from __future__ import annotations

from collections.abc import Callable

from bellwether.llm.base import (
    DEFAULT_MAX_TOKENS,
    LLMClient,
    LLMError,
    LLMResponse,
    ModelSpec,
)
from bellwether.llm.claude import ClaudeClient
from bellwether.llm.gemini import GeminiClient

REGISTRY: dict[str, Callable[[], LLMClient]] = {
    "gemini": GeminiClient,
    "claude": ClaudeClient,
}

DEFAULT_CLIENT = "gemini"


def get_client(name: str) -> LLMClient:
    """Build a backend by name, raising KeyError for anything unregistered."""
    return REGISTRY[name]()


def client_specs() -> list[ModelSpec]:
    """Every backend's spec, in registry order."""
    return [get_client(name).spec for name in REGISTRY]


__all__ = [
    "DEFAULT_CLIENT",
    "DEFAULT_MAX_TOKENS",
    "REGISTRY",
    "ClaudeClient",
    "GeminiClient",
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "ModelSpec",
    "client_specs",
    "get_client",
]
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m uv run pytest tests/bellwether/llm/test_llm.py -v`
Expected: PASS, 20 passed (the two parametrized cases contribute three each)

- [ ] **Step 8: Verify the suite is still hermetic with no keys set**

```bash
python -m uv run pytest
```
Expected: PASS. If any test reaches the network, it fails here — that is the point.

- [ ] **Step 9: Run the gates, each unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
```

- [ ] **Step 10: Commit**

```bash
git add bellwether/llm/ tests/bellwether/llm/
git commit -m "feat: the LLM provider seam — Gemini wired, Claude written and owed"
```

---

### Task 6: The LLM reranker

**Files:**
- Create: `bellwether/context/retrieval/rerank/llm.py`
- Modify: `bellwether/context/retrieval/rerank/__init__.py`
- Test: `tests/bellwether/context/retrieval/rerank/test_llm_reranker.py`

**Interfaces:**
- Consumes: `Reranker`, `RerankerSpec`, `RerankResult`, `RerankError`; `LLMClient`, `LLMError`
- Produces:
  - `RANKING_SCHEMA: dict[str, Any]`
  - `LLMReranker(client: LLMClient, candidate_depth: int = 20)`
  - `build_prompt(query: str, hits: Sequence[SearchHit]) -> str`

Structured output rather than parsed prose: a malformed ranking becomes **impossible** rather than merely unlikely. A backend failure returns the fused order untouched rather than nothing — degrading to the input ranking is correct behaviour for a reranker, and losing the whole result set is not.

- [ ] **Step 1: Write the failing test**

```python
# tests/bellwether/context/retrieval/rerank/test_llm_reranker.py
"""Reranking through a model, with a fake backend and no socket in sight."""

from __future__ import annotations

from typing import Any

from bellwether.context.embedders.base import UsageRecord
from bellwether.context.retrieval.rerank import LLMReranker
from bellwether.context.retrieval.rerank.llm import build_prompt
from bellwether.context.vectors import SearchHit
from bellwether.llm import LLMError
from bellwether.llm.base import LLMResponse, ModelSpec


def _hit(chunk_id: str, score: float, text: str = "body text") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=score,
        text=text,
        anchor=chunk_id,
        source_path=f"docs/{chunk_id}.md",
        source_type="adr",
        component="docs",
        title=chunk_id,
    )


class FakeClient:
    """Returns a scripted ranking, or raises, and records the prompt it saw."""

    def __init__(self, data: object | None = None, error: Exception | None = None) -> None:
        self.data = data
        self.error = error
        self.prompts: list[str] = []

    @property
    def spec(self) -> ModelSpec:
        return ModelSpec(
            name="fake",
            label="Fake",
            model_id="fake-1",
            hosted=False,
            cost_per_million_input=0.0,
            cost_per_million_output=0.0,
            notes="test double",
        )

    def available(self) -> tuple[bool, str]:
        return True, "ready"

    def complete(
        self, prompt: str, schema: dict[str, Any], max_tokens: int = 2048
    ) -> LLMResponse:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return LLMResponse(
            text="",
            data=self.data,
            usage=UsageRecord(
                engine="fake", texts=1, tokens=120, cost_usd=0.0, latency_ms=5.0
            ),
        )


def test_reorders_by_the_relevance_the_model_returned() -> None:
    client = FakeClient(
        data=[
            {"chunk_id": "b", "relevance": 2},
            {"chunk_id": "a", "relevance": 0},
        ]
    )
    hits = [_hit("a", 0.9), _hit("b", 0.4)]
    result = LLMReranker(client).rerank("anything", hits, limit=2)
    assert [hit.chunk_id for hit in result.hits] == ["b", "a"]


def test_reports_the_usage_the_backend_recorded() -> None:
    client = FakeClient(data=[{"chunk_id": "a", "relevance": 1}])
    result = LLMReranker(client).rerank("anything", [_hit("a", 0.9)], limit=1)
    assert result.usage is not None
    assert result.usage.tokens == 120


def test_a_backend_failure_degrades_to_the_fused_order() -> None:
    client = FakeClient(error=LLMError("fake exploded"))
    hits = [_hit("a", 0.9), _hit("b", 0.4)]
    result = LLMReranker(client).rerank("anything", hits, limit=2)
    assert [hit.chunk_id for hit in result.hits] == ["a", "b"]
    assert result.usage is None


def test_chunks_the_model_omitted_keep_their_place_behind_the_ranked_ones() -> None:
    client = FakeClient(data=[{"chunk_id": "c", "relevance": 2}])
    hits = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.1)]
    result = LLMReranker(client).rerank("anything", hits, limit=3)
    assert result.hits[0].chunk_id == "c"
    assert {hit.chunk_id for hit in result.hits} == {"a", "b", "c"}


def test_an_id_the_model_invented_is_ignored() -> None:
    client = FakeClient(data=[{"chunk_id": "does-not-exist", "relevance": 2}])
    result = LLMReranker(client).rerank("anything", [_hit("a", 0.9)], limit=1)
    assert [hit.chunk_id for hit in result.hits] == ["a"]


def test_a_non_list_response_degrades_rather_than_raising() -> None:
    client = FakeClient(data={"unexpected": "shape"})
    result = LLMReranker(client).rerank("anything", [_hit("a", 0.9)], limit=1)
    assert [hit.chunk_id for hit in result.hits] == ["a"]


def test_only_the_candidate_window_is_sent() -> None:
    client = FakeClient(data=[])
    hits = [_hit(f"c{index}", 1.0 / (index + 1)) for index in range(30)]
    LLMReranker(client, candidate_depth=5).rerank("anything", hits, limit=5)
    prompt = client.prompts[0]
    assert "c0" in prompt
    assert "c9" not in prompt


def test_empty_input_reranks_to_empty_without_calling_the_backend() -> None:
    client = FakeClient(data=[])
    result = LLMReranker(client).rerank("anything", [], limit=5)
    assert result.hits == []
    assert client.prompts == []


def test_the_prompt_names_every_candidate_and_the_query() -> None:
    prompt = build_prompt("why qdrant", [_hit("a", 0.9), _hit("b", 0.8)])
    assert "why qdrant" in prompt
    assert "a" in prompt
    assert "b" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/rerank/test_llm_reranker.py -v`
Expected: FAIL — `ImportError: cannot import name 'LLMReranker'`

- [ ] **Step 3: Write `rerank/llm.py`**

```python
# bellwether/context/retrieval/rerank/llm.py
"""Reranking by asking a model, with the answer shape guaranteed by a schema.

Two decisions worth stating.

Structured output, not parsed prose: the backend is constrained to emit
`[{chunk_id, relevance}]`, so a malformed ranking is impossible rather than merely
unlikely. Retrieval code that regex-scrapes rankings out of an LLM's paragraph is
code that fails silently on the one query that mattered.

A failure degrades to the fused order rather than to nothing. A reranker whose model
is down should hand back the ranking it was given — that is a slightly worse answer.
Handing back an empty list is no answer, and would show up in the eval as a score of
zero, which would be a measurement of the outage rather than of the reranker.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

from bellwether.context.retrieval.rerank.base import RerankerSpec, RerankResult
from bellwether.context.vectors import SearchHit
from bellwether.llm import LLMClient, LLMError

CANDIDATE_DEPTH = 20

# How much of each chunk the model sees. Enough to judge, short enough that twenty
# candidates fit in one call without the bill becoming the story.
SNIPPET_CHARS = 600

RANKING_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "chunk_id": {"type": "string"},
            "relevance": {"type": "integer", "enum": [0, 1, 2]},
        },
        "required": ["chunk_id", "relevance"],
    },
}

INSTRUCTIONS = """\
You are ranking retrieved passages from an engineering knowledge base by how well \
each one answers the question.

Grade every candidate:
  2 - fully answers the question on its own
  1 - partially answers it, or is needed context
  0 - does not answer it

Return one entry per candidate. Use the candidate ids exactly as given."""


def build_prompt(query: str, hits: Sequence[SearchHit]) -> str:
    """The ranking prompt: the question, then every candidate with its id."""
    lines = [INSTRUCTIONS, "", f"Question: {query}", "", "Candidates:"]
    for hit in hits:
        anchor = hit.anchor or "(no anchor)"
        snippet = hit.text[:SNIPPET_CHARS].replace("\n", " ")
        lines.append(f"- id: {hit.chunk_id} | {anchor} | {snippet}")
    return "\n".join(lines)


class LLMReranker:
    """Scores the candidate window through an LLM backend, then reorders."""

    def __init__(self, client: LLMClient, candidate_depth: int = CANDIDATE_DEPTH) -> None:
        self.client = client
        self.candidate_depth = candidate_depth

    @property
    def spec(self) -> RerankerSpec:
        """What this reranker is and what it costs."""
        model = self.client.spec
        return RerankerSpec(
            name=f"llm-{model.name}",
            label=f"LLM ({model.label})",
            hosted=model.hosted,
            notes=f"{model.model_id}, top-{self.candidate_depth} window",
        )

    def available(self) -> tuple[bool, str]:
        """Available exactly when its backend is."""
        return self.client.available()

    def rerank(self, query: str, hits: Sequence[SearchHit], limit: int) -> RerankResult:
        """Grade the candidate window, then order by grade and original rank."""
        if not hits:
            return RerankResult(hits=[], usage=None)

        window = list(hits[: self.candidate_depth])
        tail = list(hits[self.candidate_depth :])

        try:
            response = self.client.complete(build_prompt(query, window), RANKING_SCHEMA)
        except LLMError:
            # Degrade to what we were given. See the module docstring.
            return RerankResult(hits=list(hits[:limit]), usage=None)

        grades = _grades(response.data)
        if not grades:
            return RerankResult(hits=list(hits[:limit]), usage=response.usage)

        scored: list[tuple[int, int, SearchHit]] = []
        for position, hit in enumerate(window):
            # An unjudged candidate sits below every judged one but keeps its order.
            scored.append((-grades.get(hit.chunk_id, 0), position, hit))
        scored.sort()

        ordered = [dataclasses.replace(hit, score=float(-grade)) for grade, _, hit in scored]
        return RerankResult(hits=(ordered + tail)[:limit], usage=response.usage)


def _grades(data: object) -> dict[str, int]:
    """`{chunk_id: relevance}` from a well-formed response, else empty."""
    if not isinstance(data, list):
        return {}
    grades: dict[str, int] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        chunk_id = entry.get("chunk_id")
        relevance = entry.get("relevance")
        if isinstance(chunk_id, str) and isinstance(relevance, int):
            grades[chunk_id] = relevance
    return grades
```

- [ ] **Step 4: Extend `rerank/__init__.py`**

Add the import and export — the file becomes:

```python
# bellwether/context/retrieval/rerank/__init__.py
"""The reranker registry — everything the search service can slot in."""

from __future__ import annotations

from bellwether.context.retrieval.rerank.base import (
    Reranker,
    RerankError,
    RerankerSpec,
    RerankResult,
)
from bellwether.context.retrieval.rerank.heuristic import HeuristicReranker, HeuristicWeights
from bellwether.context.retrieval.rerank.llm import RANKING_SCHEMA, LLMReranker

__all__ = [
    "RANKING_SCHEMA",
    "HeuristicReranker",
    "HeuristicWeights",
    "LLMReranker",
    "RerankError",
    "RerankResult",
    "Reranker",
    "RerankerSpec",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/rerank/ -v`
Expected: PASS, 19 passed (10 heuristic + 9 LLM)

- [ ] **Step 6: Run the gates, each unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
python -m uv run pytest
```

- [ ] **Step 7: Commit**

```bash
git add bellwether/context/retrieval/rerank/ tests/bellwether/context/retrieval/rerank/
git commit -m "feat: LLM reranking with a guaranteed answer shape and a safe degrade"
```

---

### Task 7: The search service, and Day 7's unpaid CLI debt

**Files:**
- Create: `bellwether/context/retrieval/search.py`
- Modify: `bellwether/context/__main__.py` (currently only `--root`, `--corpus`, `--dry-run`)
- Test: `tests/bellwether/context/retrieval/test_search.py`

**Interfaces:**
- Consumes: `BM25Index`, `reciprocal_rank_fusion`, `weighted_fusion`, `Reranker`, `Embedder`, `VectorStore`
- Produces:
  - `SearchMode` (StrEnum): `LEXICAL`, `DENSE`, `HYBRID`, `HYBRID_WEIGHTED`, `HYBRID_HEURISTIC`, `HYBRID_LLM`
  - `SearchConfig(mode: SearchMode, engine: str = "hashing", limit: int = 10, candidate_depth: int = 20)`
  - `SearchService(index, store, embedder, reranker=None)`
  - `SearchService.search(query: str, config: SearchConfig) -> list[SearchHit]`

**Day 7's debt, restated.** `embed_corpus()` exists in `embedding_run.py` and **nothing calls it** — the Day 7 numbers came from an ad-hoc script, which means they are not reproducible. Task 7 wires `--chunk`, `--embed ENGINE`, `--engines all` as promised, plus `--search`.

- [ ] **Step 1: Write the failing test**

```python
# tests/bellwether/context/retrieval/test_search.py
"""One entry point, six configurations, and the engine still a parameter."""

from __future__ import annotations

from datetime import UTC, datetime

from bellwether.context.chunking.models import Chunk, build_chunk
from bellwether.context.documents import build_document
from bellwether.context.embedders import HashingEmbedder
from bellwether.context.retrieval.bm25 import BM25Index
from bellwether.context.retrieval.rerank import HeuristicReranker
from bellwether.context.retrieval.search import SearchConfig, SearchMode, SearchService
from bellwether.context.vectors import InMemoryVectorStore

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _chunk(index: int, text: str) -> Chunk:
    document = build_document(
        source_path=f"docs/adr/{index:04d}-x.md",
        source_type="adr",
        component="docs",
        title=f"ADR-{index:04d}",
        content=text,
        ingested_at=NOW,
    )
    return build_chunk(document, text, "markdown", index, f"ADR-{index:04d}", 1, 2)


def _service() -> SearchService:
    chunks = [
        _chunk(1, "The budget_micros field is enforced by the ad decision service."),
        _chunk(2, "Qdrant replaced ChromaDB because named vectors make the comparison fair."),
        _chunk(3, "Frequency capping uses Redis with a rolling window per viewer."),
        _chunk(4, "Prometheus scrapes every service and Grafana renders the dashboards."),
    ]
    embedder = HashingEmbedder()
    store = InMemoryVectorStore()
    store.ensure_collection([embedder.spec])
    store.upsert(chunks, embedder.spec.name, embedder.embed([c.text for c in chunks]).vectors)
    return SearchService(
        index=BM25Index(chunks),
        store=store,
        embedder=embedder,
        reranker=HeuristicReranker(),
    )


def test_lexical_mode_finds_the_identifier() -> None:
    hits = _service().search(
        "budget_micros", SearchConfig(mode=SearchMode.LEXICAL, engine="hashing", limit=2)
    )
    assert hits[0].chunk_id.endswith("#0001")


def test_dense_mode_returns_hits_from_the_store() -> None:
    hits = _service().search(
        "redis capping", SearchConfig(mode=SearchMode.DENSE, engine="hashing", limit=2)
    )
    assert hits


def test_hybrid_returns_at_most_the_limit() -> None:
    hits = _service().search(
        "qdrant named vectors", SearchConfig(mode=SearchMode.HYBRID, engine="hashing", limit=2)
    )
    assert len(hits) <= 2


def test_every_mode_returns_search_hits_with_provenance() -> None:
    service = _service()
    for mode in SearchMode:
        hits = service.search(
            "budget_micros", SearchConfig(mode=mode, engine="hashing", limit=3)
        )
        for hit in hits:
            assert hit.source_path.startswith("docs/adr/")
            assert hit.anchor is not None


def test_an_empty_query_returns_nothing_in_every_mode() -> None:
    service = _service()
    for mode in SearchMode:
        assert service.search("", SearchConfig(mode=mode, engine="hashing", limit=5)) == []


def test_hybrid_llm_without_a_reranker_falls_back_to_hybrid() -> None:
    chunks = [_chunk(1, "budget_micros is enforced here")]
    embedder = HashingEmbedder()
    store = InMemoryVectorStore()
    store.ensure_collection([embedder.spec])
    store.upsert(chunks, embedder.spec.name, embedder.embed([c.text for c in chunks]).vectors)
    service = SearchService(BM25Index(chunks), store, embedder, reranker=None)
    hits = service.search(
        "budget_micros", SearchConfig(mode=SearchMode.HYBRID_LLM, engine="hashing", limit=1)
    )
    assert len(hits) == 1


def test_results_are_reproducible_across_calls() -> None:
    service = _service()
    config = SearchConfig(mode=SearchMode.HYBRID, engine="hashing", limit=4)
    first = [hit.chunk_id for hit in service.search("service", config)]
    second = [hit.chunk_id for hit in service.search("service", config)]
    assert first == second
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/test_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.context.retrieval.search'`

- [ ] **Step 3: Write `search.py`**

```python
# bellwether/context/retrieval/search.py
"""One entry point for every retrieval configuration the eval compares.

Six modes, one code path. The comparison in Day 8's eval is only meaningful if the
five configurations differ in exactly the way their names claim and in no other way
— different candidate depths, different filters, or a different query embedding
between rows would make the table a comparison of accidents.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bellwether.context.embedders import Embedder
from bellwether.context.retrieval.bm25 import BM25Index
from bellwether.context.retrieval.fusion import reciprocal_rank_fusion, weighted_fusion
from bellwether.context.retrieval.rerank.base import Reranker
from bellwether.context.vectors import SearchHit, VectorStore


class SearchMode(StrEnum):
    """The configurations the comparison table has one row each for."""

    LEXICAL = "lexical"
    DENSE = "dense"
    HYBRID = "hybrid"
    HYBRID_WEIGHTED = "hybrid-weighted"
    HYBRID_HEURISTIC = "hybrid-heuristic"
    HYBRID_LLM = "hybrid-llm"


@dataclass(frozen=True)
class SearchConfig:
    """Everything that varies between rows of the comparison."""

    mode: SearchMode
    engine: str = "hashing"
    limit: int = 10
    candidate_depth: int = 20


class SearchService:
    """Retrieve, fuse, rerank — with every stage a parameter."""

    def __init__(
        self,
        index: BM25Index,
        store: VectorStore,
        embedder: Embedder,
        reranker: Reranker | None = None,
    ) -> None:
        self.index = index
        self.store = store
        self.embedder = embedder
        self.reranker = reranker

    def search(self, query: str, config: SearchConfig) -> list[SearchHit]:
        """The best `config.limit` chunks for `query` under `config.mode`."""
        if not query.strip():
            return []

        depth = max(config.candidate_depth, config.limit)

        if config.mode is SearchMode.LEXICAL:
            return self.index.search(query, limit=config.limit)
        if config.mode is SearchMode.DENSE:
            return self._dense(query, config.engine, config.limit)

        lexical = self.index.search(query, limit=depth)
        dense = self._dense(query, config.engine, depth)

        if config.mode is SearchMode.HYBRID_WEIGHTED:
            return weighted_fusion(dense, lexical, limit=config.limit)

        fused = reciprocal_rank_fusion([dense, lexical], limit=depth)

        if config.mode is SearchMode.HYBRID or self.reranker is None:
            return fused[: config.limit]
        return self.reranker.rerank(query, fused, limit=config.limit).hits

    def _dense(self, query: str, engine: str, limit: int) -> list[SearchHit]:
        """Embed the query with the same engine that embedded the corpus."""
        vector = self.embedder.embed([query]).vectors[0]
        return self.store.search(engine, vector, limit=limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m uv run pytest tests/bellwether/context/retrieval/test_search.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Pay Day 7's CLI debt — rewrite `__main__.py`**

```python
# bellwether/context/__main__.py
"""`python -m bellwether.context` — ingest, chunk, embed, and search the corpus.

Day 7 promised `--chunk`, `--embed` and `--engines all` and shipped none of them,
so its published numbers came from an ad-hoc script and are not reproducible by
anyone reading the repo. That is the gap this closes.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from pathlib import Path

from bellwether.context.chunking.models import Chunk
from bellwether.context.chunking.report import compare_strategies, format_comparison
from bellwether.context.chunking.router import chunk_corpus
from bellwether.context.config import load_env_file, settings
from bellwether.context.embedders import REGISTRY, get_embedder
from bellwether.context.embedding_run import EmbeddingRun, embed_corpus, format_runs
from bellwether.context.pipeline import format_report, ingest
from bellwether.context.retrieval.bm25 import BM25Index
from bellwether.context.retrieval.rerank import HeuristicReranker, LLMReranker
from bellwether.context.retrieval.rerank.base import Reranker
from bellwether.context.retrieval.search import SearchConfig, SearchMode, SearchService
from bellwether.context.store import JsonlDocumentStore
from bellwether.context.vectors import (
    COLLECTION,
    InMemoryVectorStore,
    QdrantVectorStore,
    SearchHit,
)
from bellwether.llm import get_client


def _build_parser() -> argparse.ArgumentParser:
    """Every verb the context layer exposes."""
    parser = argparse.ArgumentParser(description="Ingest, chunk, embed and search the corpus.")
    parser.add_argument("--root", type=Path, default=settings.repo_root)
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    parser.add_argument("--chunk", action="store_true", help="Chunk and report the comparison.")
    parser.add_argument("--embed", metavar="ENGINE", help="Embed the corpus with one engine.")
    parser.add_argument("--engines", metavar="all", help="Embed with every available engine.")
    parser.add_argument("--rebuild", action="store_true", help="Drop the collection first.")
    parser.add_argument("--search", metavar="QUERY", help="Search the corpus.")
    parser.add_argument(
        "--mode",
        default=SearchMode.HYBRID.value,
        choices=[mode.value for mode in SearchMode],
        help="Which retrieval configuration to search with.",
    )
    parser.add_argument("--engine", default="hashing", help="Which engine's vectors to search.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--qdrant", default="http://localhost:6333")
    parser.add_argument("--in-memory", action="store_true", help="Skip Qdrant entirely.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run whichever verb was asked for."""
    args = _build_parser().parse_args(argv)
    load_env_file()

    root: Path = args.root
    corpus_path: Path = args.corpus or root / settings.corpus_path
    store = JsonlDocumentStore(corpus_path)

    if args.search:
        return _search(args, store)

    report = ingest(root, store)
    if not args.dry_run:
        store.flush()
    print(format_report(report))
    print("dry run: nothing written" if args.dry_run else f"corpus at: {corpus_path}")

    documents = store.documents()
    # Bound unconditionally. Computing it inside the `if` below and then using it in
    # the `--embed` branch is an UnboundLocalError waiting for the first person who
    # passes --embed without --chunk.
    chunks: list[Chunk] = []

    if args.chunk or args.embed or args.engines:
        chunks = chunk_corpus(documents)
        print(f"\nchunks: {len(chunks)} from {len(documents)} documents")
        print(format_comparison(compare_strategies(documents)))

    if args.embed or args.engines:
        _embed(args, chunks)

    return 0


def _vector_store(args: argparse.Namespace) -> InMemoryVectorStore | QdrantVectorStore:
    """Qdrant unless asked otherwise; the in-memory store is a real fallback."""
    if args.in_memory:
        return InMemoryVectorStore()
    return QdrantVectorStore(base_url=args.qdrant, collection=COLLECTION)


def _embed(args: argparse.Namespace, chunks: list[Chunk]) -> None:
    """Embed with one engine or with every available one, and print the bill."""
    vectors = _vector_store(args)
    if args.rebuild and isinstance(vectors, QdrantVectorStore):
        vectors.drop_collection()

    names = list(REGISTRY) if args.engines == "all" else [args.embed]
    embedders = [get_embedder(name) for name in names if name]
    vectors.ensure_collection([embedder.spec for embedder in embedders])

    runs: list[EmbeddingRun] = []
    for embedder in embedders:
        available, reason = embedder.available()
        if not available:
            print(f"skipping {embedder.spec.name}: {reason}")
            continue
        runs.append(embed_corpus(chunks, embedder, vectors))

    if runs:
        print("\n" + format_runs(runs))


def _search(args: argparse.Namespace, store: JsonlDocumentStore) -> int:
    """Answer one query and print the hits with their provenance."""
    chunks = chunk_corpus(store.documents())
    embedder = get_embedder(args.engine)
    mode = SearchMode(args.mode)

    # Annotated to the protocol, not to the first implementation assigned — mypy
    # otherwise infers `HeuristicReranker` and rejects the LLM one on the next line.
    reranker: Reranker = HeuristicReranker()
    if mode is SearchMode.HYBRID_LLM:
        client = get_client("gemini")
        available, reason = client.available()
        if not available:
            print(f"llm reranking unavailable: {reason}")
            return 1
        reranker = LLMReranker(client)

    service = SearchService(BM25Index(chunks), _vector_store(args), embedder, reranker)
    hits = service.search(
        args.search, SearchConfig(mode=mode, engine=args.engine, limit=args.limit)
    )

    print(f"{args.search!r} — {mode.value} over {args.engine}, {len(hits)} hits\n")
    for rank, hit in enumerate(hits, start=1):
        anchor = hit.anchor or "(no anchor)"
        print(f"{rank:>2}. {hit.score:>8.4f}  {hit.source_path}  {anchor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Verify the CLI works end to end with no Docker and no keys**

```bash
python -m uv run python -m bellwether.context --chunk --dry-run
python -m uv run python -m bellwether.context --search "budget_micros" --mode lexical --in-memory --limit 5
```
Expected: the first prints the chunk count and strategy comparison; the second prints ranked hits with source paths. Neither needs Docker or a key.

- [ ] **Step 7: Run the gates, each unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
python -m uv run pytest
```

- [ ] **Step 8: Commit**

```bash
git add bellwether/context/retrieval/search.py bellwether/context/__main__.py tests/bellwether/context/retrieval/test_search.py
git commit -m "feat: the search service, and the CLI Day 7 promised and did not ship"
```

---

### Task 8: Metrics — nDCG, recall, MRR

**Files:**
- Create: `bellwether/eval/__init__.py`, `bellwether/eval/metrics.py`
- Test: `tests/bellwether/eval/__init__.py`, `tests/bellwether/eval/test_metrics.py`

**Interfaces:**
- Produces:
  - `RELEVANT_FROM: int = 1`
  - `dcg(gains: Sequence[int]) -> float`
  - `ndcg_at_k(ranked_ids: Sequence[str], judgements: Mapping[str, int], k: int = 10) -> float`
  - `recall_at_k(ranked_ids: Sequence[str], judgements: Mapping[str, int], k: int = 10) -> float`
  - `mrr(ranked_ids: Sequence[str], judgements: Mapping[str, int], k: int = 10) -> float`

Pure functions over `(ranked_ids, judgements)`. Nothing here knows what a `SearchHit` is, which is what makes them testable against hand-computed values rather than against the system that produced the ranking.

- [ ] **Step 1: Write the failing test**

```python
# tests/bellwether/eval/test_metrics.py
"""Metrics checked against hand-computed values, not against our own output."""

from __future__ import annotations

import math

import pytest

from bellwether.eval.metrics import dcg, mrr, ndcg_at_k, recall_at_k

JUDGEMENTS = {"a": 2, "b": 1, "c": 0, "d": 2}


def test_dcg_of_a_perfect_two_is_two() -> None:
    assert dcg([2]) == pytest.approx(2.0)


def test_dcg_discounts_by_log2_of_position_plus_one() -> None:
    # gain 2 at rank 1 -> 2/log2(2) = 2 ; gain 1 at rank 2 -> 1/log2(3)
    assert dcg([2, 1]) == pytest.approx(2.0 + 1 / math.log2(3))


def test_ndcg_is_one_for_the_ideal_ordering() -> None:
    assert ndcg_at_k(["a", "d", "b", "c"], JUDGEMENTS, k=4) == pytest.approx(1.0)


def test_ndcg_is_lower_for_a_worse_ordering() -> None:
    ideal = ndcg_at_k(["a", "d", "b"], JUDGEMENTS, k=3)
    worse = ndcg_at_k(["b", "c", "a"], JUDGEMENTS, k=3)
    assert worse < ideal


def test_ndcg_is_zero_when_nothing_relevant_is_retrieved() -> None:
    assert ndcg_at_k(["c"], JUDGEMENTS, k=1) == 0.0


def test_ndcg_with_no_relevant_judgements_is_zero_not_a_crash() -> None:
    assert ndcg_at_k(["a"], {"a": 0}, k=1) == 0.0


def test_an_unjudged_id_counts_as_zero() -> None:
    assert ndcg_at_k(["unjudged"], JUDGEMENTS, k=1) == 0.0


def test_recall_counts_grade_one_and_above() -> None:
    # relevant set is {a, b, d}; retrieving a and b is 2/3
    assert recall_at_k(["a", "b", "c"], JUDGEMENTS, k=3) == pytest.approx(2 / 3)


def test_recall_is_one_when_everything_relevant_is_found() -> None:
    assert recall_at_k(["a", "b", "d"], JUDGEMENTS, k=3) == pytest.approx(1.0)


def test_recall_with_no_relevant_judgements_is_zero() -> None:
    assert recall_at_k(["a"], {"a": 0}, k=1) == 0.0


def test_mrr_is_one_over_the_first_relevant_rank() -> None:
    assert mrr(["c", "a"], JUDGEMENTS, k=2) == pytest.approx(0.5)


def test_mrr_is_zero_when_nothing_relevant_is_in_the_window() -> None:
    assert mrr(["c"], JUDGEMENTS, k=1) == 0.0


def test_k_truncates_the_ranking() -> None:
    assert mrr(["c", "a"], JUDGEMENTS, k=1) == 0.0


def test_an_empty_ranking_scores_zero_everywhere() -> None:
    assert ndcg_at_k([], JUDGEMENTS) == 0.0
    assert recall_at_k([], JUDGEMENTS) == 0.0
    assert mrr([], JUDGEMENTS) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m uv run pytest tests/bellwether/eval/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.eval'`

- [ ] **Step 3: Write the implementation**

Create `bellwether/eval/__init__.py` as an empty file, then:

```python
# bellwether/eval/metrics.py
"""What "better retrieval" means, as arithmetic rather than as an adjective.

Three metrics because each answers a different question a real user has. nDCG@10
asks whether the good answers are near the top and grades partial answers as
partial. recall@10 asks the blunter question — did the answer make the cut at all.
MRR asks how far a human scrolls before the first useful thing.

Deliberately pure: these take ranked ids and a judgement map, and know nothing about
`SearchHit`, embeddings, or which configuration produced the ranking. That is what
lets them be checked against values computed by hand rather than against our own
output, which would only prove the code agrees with itself.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

# Grades are 0 (irrelevant), 1 (partially answers), 2 (fully answers). Anything at
# or above 1 counts as relevant for the set-based metrics.
RELEVANT_FROM = 1

DEFAULT_K = 10


def dcg(gains: Sequence[int]) -> float:
    """Discounted cumulative gain: each gain divided by log2 of its rank plus one."""
    return sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))


def ndcg_at_k(
    ranked_ids: Sequence[str], judgements: Mapping[str, int], k: int = DEFAULT_K
) -> float:
    """nDCG@k — graded and rank-aware. Unjudged ids score zero."""
    gains = [judgements.get(chunk_id, 0) for chunk_id in ranked_ids[:k]]
    ideal = sorted(judgements.values(), reverse=True)[:k]
    best = dcg(ideal)
    if best == 0:
        return 0.0
    return dcg(gains) / best


def recall_at_k(
    ranked_ids: Sequence[str], judgements: Mapping[str, int], k: int = DEFAULT_K
) -> float:
    """What share of the relevant chunks appear in the top k."""
    relevant = {
        chunk_id for chunk_id, grade in judgements.items() if grade >= RELEVANT_FROM
    }
    if not relevant:
        return 0.0
    found = relevant & set(ranked_ids[:k])
    return len(found) / len(relevant)


def mrr(ranked_ids: Sequence[str], judgements: Mapping[str, int], k: int = DEFAULT_K) -> float:
    """One over the rank of the first relevant chunk, or zero if there is none."""
    for rank, chunk_id in enumerate(ranked_ids[:k], start=1):
        if judgements.get(chunk_id, 0) >= RELEVANT_FROM:
            return 1 / rank
    return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m uv run pytest tests/bellwether/eval/test_metrics.py -v`
Expected: PASS, 14 passed

- [ ] **Step 5: Run the gates, each unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
python -m uv run pytest
```

- [ ] **Step 6: Commit**

```bash
git add bellwether/eval/ tests/bellwether/eval/
git commit -m "feat: nDCG, recall and MRR as pure functions"
```

---

### Task 9: The gold set and the pooling harness

**Files:**
- Create: `bellwether/eval/gold.py`, `bellwether/eval/pooling.py`
- Modify: `.gitignore`
- Test: `tests/bellwether/eval/test_gold.py`, `tests/bellwether/eval/test_pooling.py`

**Interfaces:**
- Consumes: `SearchHit`
- Produces:
  - `Category` (StrEnum): `IDENTIFIER`, `CONCEPTUAL`, `CROSS_DOCUMENT`
  - `GoldQuery` (Pydantic): `query_id`, `text`, `category`, `judgements: dict[str, int]`
  - `GoldSet` (Pydantic): `version`, `created_at`, `notes`, `queries: list[GoldQuery]`
  - `GoldSet.relevant(query_id) -> set[str]`
  - `load_gold_set(path: Path) -> GoldSet`, `save_gold_set(goldset, path) -> None`
  - `PoolEntry` (frozen dataclass): `query_id`, `chunk_id`, `anchor`, `source_path`, `text`
  - `build_pool(queries, rankings: Mapping[str, Mapping[str, Sequence[SearchHit]]], depth=10, seed=8) -> list[PoolEntry]`
  - `pool_coverage(goldset, rankings, k=10) -> float`

**First, the gitignore collision.** `.gitignore` currently contains a bare `data/`, so **`data/gold/` would silently not be committed** — and the spec requires the answer key to be in the repo. It must simultaneously stay out of ingestion, which `discovery.py` already guarantees by excluding any path segment named `data`. Git will not descend into a directory ignored by a bare `data/` line, so a `!data/gold/` negation alone does nothing; the parent must become `data/*`.

- [ ] **Step 1: Fix `.gitignore` so the answer key is committable but never ingested**

Replace the final `data/` line with:

```gitignore
# The corpus and the vectors are build output. The gold set is not — it is the
# answer key the retrieval numbers are measured against, and a published number
# whose answer key is untracked is a number nobody can check.
#
# `data/*` rather than `data/`: git will not descend into a directory ignored by a
# bare rule, so the negation below would never be consulted.
data/*
!data/gold/
```

Verify both properties hold:

```bash
git check-ignore -v data/context/corpus.jsonl
mkdir -p data/gold && echo '{}' > data/gold/probe.json
git status --short data/gold/
rm data/gold/probe.json
```
Expected: the first prints a matching `data/*` rule; the second prints `?? data/gold/probe.json`, proving the negation works.

- [ ] **Step 2: Write the failing tests**

```python
# tests/bellwether/eval/test_gold.py
"""The answer key — validated on load, because a bad one silently skews everything."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from bellwether.eval.gold import Category, GoldQuery, GoldSet, load_gold_set, save_gold_set

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _query(query_id: str = "q001") -> GoldQuery:
    return GoldQuery(
        query_id=query_id,
        text="where is budget_micros enforced",
        category=Category.IDENTIFIER,
        judgements={"a#0001": 2, "a#0002": 0},
    )


def test_relevant_returns_grades_at_or_above_one() -> None:
    goldset = GoldSet(version="1", created_at=NOW, notes="", queries=[_query()])
    assert goldset.relevant("q001") == {"a#0001"}


def test_a_grade_outside_zero_to_two_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GoldQuery(
            query_id="q001",
            text="x",
            category=Category.IDENTIFIER,
            judgements={"a#0001": 3},
        )


def test_a_query_with_no_relevant_chunk_is_rejected() -> None:
    # An unanswerable query scores every configuration zero and moves the mean for
    # no reason. Catch it at load, not in the published table.
    with pytest.raises(ValidationError):
        GoldQuery(
            query_id="q001",
            text="x",
            category=Category.IDENTIFIER,
            judgements={"a#0001": 0},
        )


def test_duplicate_query_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        GoldSet(version="1", created_at=NOW, notes="", queries=[_query(), _query()])


def test_round_trips_through_disk(tmp_path: Path) -> None:
    goldset = GoldSet(version="1", created_at=NOW, notes="n", queries=[_query()])
    path = tmp_path / "gold.json"
    save_gold_set(goldset, path)
    assert load_gold_set(path).queries[0].judgements == {"a#0001": 2, "a#0002": 0}


def test_categories_are_the_three_the_report_breaks_out() -> None:
    assert {member.value for member in Category} == {
        "identifier",
        "conceptual",
        "cross_document",
    }
```

```python
# tests/bellwether/eval/test_pooling.py
"""The pool — shuffled, stripped of provenance, and drawn from every configuration."""

from __future__ import annotations

from datetime import UTC, datetime

from bellwether.context.vectors import SearchHit
from bellwether.eval.gold import Category, GoldQuery, GoldSet
from bellwether.eval.pooling import build_pool, pool_coverage

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _hit(chunk_id: str, score: float = 1.0) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=score,
        text=f"text of {chunk_id}",
        anchor=chunk_id,
        source_path=f"docs/{chunk_id}.md",
        source_type="adr",
        component="docs",
        title=chunk_id,
    )


def _query(query_id: str = "q001", judgements: dict[str, int] | None = None) -> GoldQuery:
    return GoldQuery(
        query_id=query_id,
        text="anything",
        category=Category.CONCEPTUAL,
        judgements=judgements or {"a": 2},
    )


def test_pools_the_union_of_every_configuration() -> None:
    rankings = {
        "q001": {
            "lexical": [_hit("a"), _hit("b")],
            "dense": [_hit("c")],
            "hybrid-llm": [_hit("d")],
        }
    }
    entries = build_pool([_query()], rankings, depth=10)
    assert {entry.chunk_id for entry in entries} == {"a", "b", "c", "d"}


def test_a_chunk_seen_by_two_configurations_appears_once() -> None:
    rankings = {"q001": {"lexical": [_hit("a")], "dense": [_hit("a")]}}
    assert len(build_pool([_query()], rankings, depth=10)) == 1


def test_depth_truncates_each_configuration_before_pooling() -> None:
    rankings = {"q001": {"lexical": [_hit("a"), _hit("b"), _hit("c")]}}
    entries = build_pool([_query()], rankings, depth=2)
    assert {entry.chunk_id for entry in entries} == {"a", "b"}


def test_the_pool_entry_carries_no_hint_of_which_system_found_it() -> None:
    rankings = {"q001": {"lexical": [_hit("a")]}}
    entry = build_pool([_query()], rankings, depth=10)[0]
    assert not hasattr(entry, "mode")
    assert not hasattr(entry, "score")


def test_the_shuffle_is_seeded_so_judging_is_reproducible() -> None:
    rankings = {"q001": {"lexical": [_hit(letter) for letter in "abcdefgh"]}}
    first = [entry.chunk_id for entry in build_pool([_query()], rankings, depth=10, seed=8)]
    second = [entry.chunk_id for entry in build_pool([_query()], rankings, depth=10, seed=8)]
    assert first == second


def test_the_shuffle_does_not_preserve_retrieval_order() -> None:
    # If the pool arrived in rank order, a judge would anchor on the first system's
    # opinion — which is exactly the bias pooling exists to remove.
    ordered = [_hit(f"c{index:02d}") for index in range(20)]
    entries = build_pool([_query()], {"q001": {"lexical": ordered}}, depth=20, seed=8)
    assert [entry.chunk_id for entry in entries] != [hit.chunk_id for hit in ordered]


def test_coverage_is_one_when_every_retrieved_chunk_was_judged() -> None:
    goldset = GoldSet(
        version="1",
        created_at=NOW,
        notes="",
        queries=[_query(judgements={"a": 2, "b": 0})],
    )
    rankings = {"q001": {"hybrid": [_hit("a"), _hit("b")]}}
    assert pool_coverage(goldset, rankings) == 1.0


def test_coverage_falls_when_a_configuration_surfaces_an_unjudged_chunk() -> None:
    goldset = GoldSet(
        version="1", created_at=NOW, notes="", queries=[_query(judgements={"a": 2})]
    )
    rankings = {"q001": {"hybrid": [_hit("a"), _hit("unjudged")]}}
    assert pool_coverage(goldset, rankings) == 0.5
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m uv run pytest tests/bellwether/eval/test_gold.py tests/bellwether/eval/test_pooling.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.eval.gold'`

- [ ] **Step 4: Write `eval/gold.py`**

```python
# bellwether/eval/gold.py
"""The answer key, and the validation that keeps it honest.

Two rules are enforced at load rather than trusted. A grade outside 0-2 is a typo
that would quietly distort nDCG. A query with nothing relevant scores every
configuration zero and drags the mean down identically for all of them, which looks
like data and is actually noise.

The file lives under `data/gold/` — committed via a `.gitignore` negation, and never
ingested, because `discovery.py` excludes every path with a `data` segment. Both
properties matter: an answer key nobody can check is not evidence, and an answer key
inside the searchable corpus is contamination.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, field_validator, model_validator

# Grades: 0 irrelevant, 1 partially answers or is needed context, 2 fully answers.
MIN_GRADE = 0
MAX_GRADE = 2
RELEVANT_FROM = 1


class Category(StrEnum):
    """The three question shapes the comparison reports separately."""

    IDENTIFIER = "identifier"
    CONCEPTUAL = "conceptual"
    CROSS_DOCUMENT = "cross_document"


class GoldQuery(BaseModel):
    """One question, and every chunk that was judged against it."""

    query_id: str
    text: str
    category: Category
    judgements: dict[str, int]

    @field_validator("judgements")
    @classmethod
    def _grades_in_range(cls, value: dict[str, int]) -> dict[str, int]:
        """Every grade must be 0, 1 or 2."""
        for chunk_id, grade in value.items():
            if grade < MIN_GRADE or grade > MAX_GRADE:
                raise ValueError(f"grade {grade} for {chunk_id} is outside 0-2")
        return value

    @model_validator(mode="after")
    def _has_a_relevant_chunk(self) -> GoldQuery:
        """A query nothing answers measures nothing."""
        if not any(grade >= RELEVANT_FROM for grade in self.judgements.values()):
            raise ValueError(f"{self.query_id} has no chunk graded 1 or above")
        return self


class GoldSet(BaseModel):
    """Every judged query, plus how and when it was built."""

    version: str
    created_at: datetime
    notes: str
    queries: list[GoldQuery]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> GoldSet:
        """Two queries sharing an id would silently overwrite one another."""
        seen = [query.query_id for query in self.queries]
        if len(seen) != len(set(seen)):
            raise ValueError("duplicate query_id in the gold set")
        return self

    def relevant(self, query_id: str) -> set[str]:
        """Every chunk graded 1 or above for this query."""
        for query in self.queries:
            if query.query_id == query_id:
                return {
                    chunk_id
                    for chunk_id, grade in query.judgements.items()
                    if grade >= RELEVANT_FROM
                }
        return set()

    def by_category(self, category: Category) -> list[GoldQuery]:
        """Every query of one shape, for the per-category breakdown."""
        return [query for query in self.queries if query.category is category]


def load_gold_set(path: Path) -> GoldSet:
    """Read and validate the answer key."""
    return GoldSet.model_validate_json(path.read_text(encoding="utf-8"))


def save_gold_set(goldset: GoldSet, path: Path) -> None:
    """Write the answer key, indented so its diffs are reviewable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(goldset.model_dump_json())
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

- [ ] **Step 5: Write `eval/pooling.py`**

```python
# bellwether/eval/pooling.py
"""Build the judging pool so that no configuration can win by defining the key.

The pool is the union of the top results from *every* configuration that produces a
ranking, not just the retrieval ones. This is easy to get wrong and expensive when
you do: reranking reorders a window of the fused list, so a chunk sitting at rank 14
can be promoted into a reranked top-10 while being absent from a pool built only
from hybrid's top-10. It would then go unjudged, score zero, and silently penalise
the reranker for working.

Entries are shuffled and carry no score, rank, or originating mode. A judge who can
see which system found a chunk — or who reads the chunks in the order one system
ranked them — anchors on that system's opinion, and the answer key stops being
independent of the thing it measures.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bellwether.context.vectors import SearchHit
from bellwether.eval.gold import GoldQuery, GoldSet

DEFAULT_DEPTH = 10
DEFAULT_SEED = 8

# Rankings keyed query_id -> mode name -> that mode's hits.
Rankings = Mapping[str, Mapping[str, Sequence[SearchHit]]]


@dataclass(frozen=True)
class PoolEntry:
    """One thing to judge. Deliberately carries no hint of where it came from."""

    query_id: str
    chunk_id: str
    anchor: str | None
    source_path: str
    text: str


def build_pool(
    queries: Sequence[GoldQuery],
    rankings: Rankings,
    depth: int = DEFAULT_DEPTH,
    seed: int = DEFAULT_SEED,
) -> list[PoolEntry]:
    """The shuffled union of every configuration's top `depth`, per query."""
    entries: list[PoolEntry] = []
    for query in queries:
        by_mode = rankings.get(query.query_id, {})
        seen: dict[str, SearchHit] = {}
        for hits in by_mode.values():
            for hit in hits[:depth]:
                seen.setdefault(hit.chunk_id, hit)
        entries.extend(
            PoolEntry(
                query_id=query.query_id,
                chunk_id=hit.chunk_id,
                anchor=hit.anchor,
                source_path=hit.source_path,
                text=hit.text,
            )
            # Sorted before shuffling so the shuffle is the only source of order and
            # a seeded run is reproducible on any machine.
            for hit in [seen[key] for key in sorted(seen)]
        )

    random.Random(seed).shuffle(entries)
    return entries


def pool_coverage(goldset: GoldSet, rankings: Rankings, k: int = DEFAULT_DEPTH) -> float:
    """What share of retrieved chunks were actually judged.

    Pooling's known limitation, reported rather than hidden: a relevant chunk that no
    configuration retrieved was never judged and counts against nobody.
    """
    retrieved = 0
    judged = 0
    for query in goldset.queries:
        graded = set(query.judgements)
        for hits in rankings.get(query.query_id, {}).values():
            for hit in hits[:k]:
                retrieved += 1
                if hit.chunk_id in graded:
                    judged += 1
    if retrieved == 0:
        return 0.0
    return judged / retrieved
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m uv run pytest tests/bellwether/eval/ -v`
Expected: PASS, 28 passed (14 metrics + 6 gold + 8 pooling)

- [ ] **Step 7: Run the gates, each unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
python -m uv run pytest
```

- [ ] **Step 8: Commit**

```bash
git add .gitignore bellwether/eval/ tests/bellwether/eval/
git commit -m "feat: the gold set, the pooling harness, and a gitignore that tracks the answer key"
```

---

### Task 10: The comparison report

**Files:**
- Create: `bellwether/eval/report.py`
- Test: `tests/bellwether/eval/test_report.py`

**Interfaces:**
- Consumes: `GoldSet`, `Category`, `ndcg_at_k`, `recall_at_k`, `mrr`, `pool_coverage`, `SearchHit`, `SearchMode`
- Produces:
  - `ConfigurationResult` (frozen dataclass): `mode`, `queries`, `ndcg`, `recall`, `reciprocal_rank`, `latency_ms`, `cost_usd`
  - `evaluate(goldset, rankings, latencies=None, costs=None, k=10) -> list[ConfigurationResult]`
  - `evaluate_category(goldset, rankings, category, k=10) -> list[ConfigurationResult]`
  - `format_results(results, title) -> str`
  - `format_markdown(results) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/bellwether/eval/test_report.py
"""Turning rankings into the table the day publishes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bellwether.context.vectors import SearchHit
from bellwether.eval.gold import Category, GoldQuery, GoldSet
from bellwether.eval.report import evaluate, evaluate_category, format_results

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _hit(chunk_id: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=1.0,
        text="text",
        anchor=chunk_id,
        source_path=f"docs/{chunk_id}.md",
        source_type="adr",
        component="docs",
        title=chunk_id,
    )


def _goldset() -> GoldSet:
    return GoldSet(
        version="1",
        created_at=NOW,
        notes="",
        queries=[
            GoldQuery(
                query_id="q1",
                text="identifier question",
                category=Category.IDENTIFIER,
                judgements={"a": 2, "b": 0},
            ),
            GoldQuery(
                query_id="q2",
                text="conceptual question",
                category=Category.CONCEPTUAL,
                judgements={"c": 2, "d": 1},
            ),
        ],
    )


def test_a_perfect_configuration_scores_one() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}, "q2": {"lexical": [_hit("c"), _hit("d")]}}
    result = evaluate(_goldset(), rankings)[0]
    assert result.ndcg == pytest.approx(1.0)
    assert result.recall == pytest.approx(1.0)


def test_a_configuration_that_finds_nothing_scores_zero() -> None:
    rankings = {"q1": {"lexical": [_hit("b")]}, "q2": {"lexical": [_hit("z")]}}
    result = evaluate(_goldset(), rankings)[0]
    assert result.ndcg == 0.0
    assert result.recall == 0.0


def test_scores_are_averaged_over_the_queries() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}, "q2": {"lexical": [_hit("z")]}}
    result = evaluate(_goldset(), rankings)[0]
    assert result.ndcg == pytest.approx(0.5)
    assert result.queries == 2


def test_one_result_per_configuration() -> None:
    rankings = {
        "q1": {"lexical": [_hit("a")], "hybrid": [_hit("a")]},
        "q2": {"lexical": [_hit("c")], "hybrid": [_hit("c")]},
    }
    assert {result.mode for result in evaluate(_goldset(), rankings)} == {"lexical", "hybrid"}


def test_category_breakdown_scores_only_that_category() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}, "q2": {"lexical": [_hit("z")]}}
    identifier = evaluate_category(_goldset(), rankings, Category.IDENTIFIER)[0]
    conceptual = evaluate_category(_goldset(), rankings, Category.CONCEPTUAL)[0]
    assert identifier.ndcg == pytest.approx(1.0)
    assert conceptual.ndcg == 0.0


def test_cost_and_latency_are_carried_through_when_supplied() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}, "q2": {"lexical": [_hit("c")]}}
    result = evaluate(
        _goldset(), rankings, latencies={"lexical": 12.5}, costs={"lexical": 0.0123}
    )[0]
    assert result.latency_ms == pytest.approx(12.5)
    assert result.cost_usd == pytest.approx(0.0123)


def test_the_table_names_every_configuration_and_metric() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}, "q2": {"lexical": [_hit("c")]}}
    table = format_results(evaluate(_goldset(), rankings), title="All queries")
    assert "All queries" in table
    assert "lexical" in table
    assert "nDCG@10" in table


def test_an_unranked_query_scores_zero_rather_than_crashing() -> None:
    rankings = {"q1": {"lexical": [_hit("a")]}}
    result = evaluate(_goldset(), rankings)[0]
    assert result.queries == 2
    assert result.ndcg == pytest.approx(0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m uv run pytest tests/bellwether/eval/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.eval.report'`

- [ ] **Step 3: Write the implementation**

```python
# bellwether/eval/report.py
"""The table Day 8 publishes, per configuration and per question shape.

The per-category split is the point, not a detail. A headline that says hybrid beats
vector by some percentage, while hiding that the entire margin came from identifier
queries and that it lost on conceptual ones, is exactly the kind of result this
project exists not to produce.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bellwether.eval.gold import Category, GoldQuery, GoldSet
from bellwether.eval.metrics import DEFAULT_K, mrr, ndcg_at_k, recall_at_k
from bellwether.eval.pooling import Rankings


@dataclass(frozen=True)
class ConfigurationResult:
    """One row of the comparison."""

    mode: str
    queries: int
    ndcg: float
    recall: float
    reciprocal_rank: float
    latency_ms: float | None = None
    cost_usd: float | None = None


def _modes(rankings: Rankings) -> list[str]:
    """Every configuration name that appears anywhere, in stable order."""
    names: set[str] = set()
    for by_mode in rankings.values():
        names.update(by_mode)
    return sorted(names)


def _score(
    queries: Sequence[GoldQuery],
    rankings: Rankings,
    mode: str,
    k: int,
) -> tuple[float, float, float]:
    """Mean nDCG, recall and reciprocal rank for one configuration."""
    if not queries:
        return 0.0, 0.0, 0.0

    ndcg_total = 0.0
    recall_total = 0.0
    rank_total = 0.0
    for query in queries:
        # A query this configuration produced no ranking for scores zero rather than
        # being dropped — silently excluding it would flatter the configuration.
        hits = rankings.get(query.query_id, {}).get(mode, [])
        ranked = [hit.chunk_id for hit in hits]
        ndcg_total += ndcg_at_k(ranked, query.judgements, k)
        recall_total += recall_at_k(ranked, query.judgements, k)
        rank_total += mrr(ranked, query.judgements, k)

    count = len(queries)
    return ndcg_total / count, recall_total / count, rank_total / count


def evaluate(
    goldset: GoldSet,
    rankings: Rankings,
    latencies: Mapping[str, float] | None = None,
    costs: Mapping[str, float] | None = None,
    k: int = DEFAULT_K,
) -> list[ConfigurationResult]:
    """One result per configuration, over every query in the gold set."""
    return _evaluate(goldset.queries, rankings, latencies, costs, k)


def evaluate_category(
    goldset: GoldSet,
    rankings: Rankings,
    category: Category,
    k: int = DEFAULT_K,
) -> list[ConfigurationResult]:
    """One result per configuration, over one question shape."""
    return _evaluate(goldset.by_category(category), rankings, None, None, k)


def _evaluate(
    queries: Sequence[GoldQuery],
    rankings: Rankings,
    latencies: Mapping[str, float] | None,
    costs: Mapping[str, float] | None,
    k: int,
) -> list[ConfigurationResult]:
    """Score every configuration over `queries`."""
    results: list[ConfigurationResult] = []
    for mode in _modes(rankings):
        ndcg, recall, rank = _score(queries, rankings, mode, k)
        results.append(
            ConfigurationResult(
                mode=mode,
                queries=len(queries),
                ndcg=ndcg,
                recall=recall,
                reciprocal_rank=rank,
                latency_ms=(latencies or {}).get(mode),
                cost_usd=(costs or {}).get(mode),
            )
        )
    return results


def format_results(results: Sequence[ConfigurationResult], title: str) -> str:
    """The comparison as a fixed-width table for the terminal and the devlog."""
    header = (
        f"{'configuration':<20}{'queries':>9}{'nDCG@10':>10}"
        f"{'recall@10':>11}{'MRR':>8}{'p50 ms':>9}{'cost USD':>11}"
    )
    lines = [title, "=" * len(header), header, "-" * len(header)]
    for result in results:
        latency = f"{result.latency_ms:.1f}" if result.latency_ms is not None else "-"
        cost = f"{result.cost_usd:.4f}" if result.cost_usd is not None else "-"
        lines.append(
            f"{result.mode:<20}{result.queries:>9}{result.ndcg:>10.3f}"
            f"{result.recall:>11.3f}{result.reciprocal_rank:>8.3f}{latency:>9}{cost:>11}"
        )
    return "\n".join(lines)


def format_markdown(results: Sequence[ConfigurationResult]) -> str:
    """The same table as Markdown, for the devlog and the running doc."""
    lines = [
        "| Configuration | nDCG@10 | recall@10 | MRR | p50 ms | cost USD |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        latency = f"{result.latency_ms:.1f}" if result.latency_ms is not None else "—"
        cost = f"{result.cost_usd:.4f}" if result.cost_usd is not None else "—"
        lines.append(
            f"| {result.mode} | {result.ndcg:.3f} | {result.recall:.3f} "
            f"| {result.reciprocal_rank:.3f} | {latency} | {cost} |"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m uv run pytest tests/bellwether/eval/test_report.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Run the gates, each unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
python -m uv run pytest
```

- [ ] **Step 6: Commit**

```bash
git add bellwether/eval/report.py tests/bellwether/eval/test_report.py
git commit -m "feat: the retrieval comparison report, split by question shape"
```

---

### Task 11: Build the gold set, and run it for real

**Files:**
- Create: `data/gold/day08-retrieval.json`
- Modify: `bellwether/context/__main__.py` (add `--eval`)

**This is the task that produces the day's number, and the one with no test that can tell you it is finished.** Order matters and git records it: **queries are written and committed before any retrieval runs.** A query set committed after seeing results is a query set tuned to results.

- [ ] **Step 1: Start Qdrant and confirm the corpus is embedded**

```bash
docker compose up -d qdrant
python -m uv run python -m bellwether.context --engines all
```
Expected: the chunk count (~585 from ~65 documents), then one row per available engine with real tokens and cost. Engines without a key are skipped by name and reason, not crashed on.

> Day 7 execution note 1: every unit test passed against a fake Qdrant while the real one silently destroyed data. Confirm the store is genuinely populated before trusting anything downstream:
> ```bash
> curl -s http://localhost:6333/collections/bellwether_context | python -m json.tool
> ```

- [ ] **Step 2: Write ~30 queries with no judgements, and commit them first**

Create `data/gold/day08-retrieval.json` with `version`, `created_at`, `notes`, and a `queries` array. Each query needs `query_id`, `text`, `category`, and — at this stage — a single placeholder judgement so the model validates.

Write ten per category, drawn from what the corpus actually contains:

- **`identifier`** — `budget_micros`, `ad_candidates_filtered_total`, `POST /ad-request`, `spend:<campaign>:<date>`, `chunk_id`, `bad_config_deploy`, `HashingEmbedder`, `point_id`, `frequency cap Redis key`, `UpsertOutcome`
- **`conceptual`** — why Qdrant over ChromaDB, why hosted embeddings over local, why the simulator reports impressions rather than the decision service, what anchor coverage measures, why gates must not be piped, what the substrate is for, why RRF over weighted fusion, what the eval harness scores, why ADR-0005 uses the public API, what makes a chunk citable
- **`cross_document`** — what breaks when a campaign budget overflows 32 bits, how a fill-rate collapse is diagnosed end to end, what happens between an ad request and a stored impression, which decisions changed after being measured, what the failure-injection path touches

```bash
git add data/gold/day08-retrieval.json
git commit -m "eval: the Day 8 gold queries, committed before any retrieval runs"
```

- [ ] **Step 3: Add `--eval` to the CLI**

Add to `_build_parser()`:

```python
    parser.add_argument("--eval", action="store_true", help="Score every configuration.")
    parser.add_argument(
        "--gold",
        type=Path,
        default=Path("data/gold/day08-retrieval.json"),
        help="The answer key to score against.",
    )
    parser.add_argument("--pool", action="store_true", help="Print the judging pool.")
```

Add to `main()`, immediately after the `--search` branch:

```python
    if args.eval or args.pool:
        return _evaluate(args, store)
```

And the function itself:

```python
def _evaluate(args: argparse.Namespace, store: JsonlDocumentStore) -> int:
    """Run every configuration over the gold set, then pool or score."""
    goldset = load_gold_set(args.gold)
    chunks = chunk_corpus(store.documents())
    index = BM25Index(chunks)
    embedder = get_embedder(args.engine)
    vectors = _vector_store(args)

    rerankers: dict[SearchMode, Reranker] = {SearchMode.HYBRID_HEURISTIC: HeuristicReranker()}
    client = get_client("gemini")
    if client.available()[0]:
        rerankers[SearchMode.HYBRID_LLM] = LLMReranker(client)

    rankings: dict[str, dict[str, list[SearchHit]]] = {}
    latencies: dict[str, float] = {}
    cost: dict[str, float] = {}

    for query in goldset.queries:
        rankings[query.query_id] = {}
        for mode in SearchMode:
            reranker = rerankers.get(mode)
            if mode in rerankers or mode not in (
                SearchMode.HYBRID_HEURISTIC,
                SearchMode.HYBRID_LLM,
            ):
                service = SearchService(index, vectors, embedder, reranker)
                started = time.perf_counter()
                hits = service.search(
                    query.text, SearchConfig(mode=mode, engine=args.engine, limit=10)
                )
                latencies[mode.value] = (time.perf_counter() - started) * 1000
                rankings[query.query_id][mode.value] = hits

    if args.pool:
        for entry in build_pool(goldset.queries, rankings):
            print(f"{entry.query_id}\t{entry.chunk_id}\t{entry.anchor}\t{entry.source_path}")
        return 0

    print(format_results(evaluate(goldset, rankings, latencies, cost), "All queries"))
    for category in Category:
        print()
        print(
            format_results(
                evaluate_category(goldset, rankings, category), f"Category: {category.value}"
            )
        )
    print(f"\npool coverage: {pool_coverage(goldset, rankings):.1%}")
    return 0
```

Add these to the top-level import block. Task 7 dropped `time` and `SearchHit` because
nothing used them yet — `_evaluate` is what uses them, so they come back here:

```python
import time

from bellwether.context.vectors import SearchHit
from bellwether.eval.gold import Category, load_gold_set
from bellwether.eval.pooling import build_pool, pool_coverage
from bellwether.eval.report import evaluate, evaluate_category, format_results
```

Merge each into the existing import block in the right group rather than appending a
second block — ruff's isort rules will fail the gate otherwise.

- [ ] **Step 4: Build the pool**

```bash
python -m uv run python -m bellwether.context --pool --engine gemini > pool.tsv
```
Expected: one shuffled line per (query, candidate), provenance-free.

- [ ] **Step 5: Judge the pool**

Grade every pooled chunk 0 / 1 / 2 by reading the chunk against the query. Write the grades into `judgements` in `data/gold/day08-retrieval.json`, replacing the placeholders. **Do not look at which configuration produced a chunk while judging** — that is the property the shuffle exists to protect.

Then hand the file to the user for review before it becomes an answer key anyone trusts.

- [ ] **Step 6: Run the comparison for real, and record the output verbatim**

```bash
python -m uv run python -m bellwether.context --eval --engine gemini
```
Expected: the all-queries table, three per-category tables, and pool coverage.

Save the exact output. **Every number published on the site and in the devlog comes from this run** — no estimates, no rounding into a better story (the Day 7 constraint, still binding).

> If `PYTHONIOENCODING` is unset on Windows, the anchor separator prints as `?` under cp1252 (Day 7 note 9). The data is fine; the terminal is lying. Set `PYTHONIOENCODING=utf-8` before reading numbers off the screen for a video.

- [ ] **Step 7: Commit the judged gold set and the wiring**

```bash
git add data/gold/day08-retrieval.json bellwether/context/__main__.py
git commit -m "eval: judge the Day 8 pool, and wire the comparison into the CLI"
```

---

### Task 12: Decisions, docs, and definition of done

**Files:**
- Create: `docs/adr/0009-hybrid-retrieval-with-rrf.md`, `docs/adr/0010-llm-reranking-behind-a-protocol.md`, `docs/devlog/day-08.md`
- Modify: `docs/site/index.html`

- [ ] **Step 1: Write ADR-0009 — hybrid retrieval with RRF**

Follow the format of `docs/adr/0008-*.md`. Content: RRF uses rank alone, so it needs no normalisation and offers no knob to tune toward a preferred outcome; weighted fusion ships as a measured alternative and appears as its own row. *Rejected:* score normalisation as the default (every scheme is a tuning knob); learned fusion weights (nothing to train on at 30 queries, and fitting weights on the eval set is the same circularity the pooling design rejects). **Trigger:** if weighted fusion beats RRF across all three categories on a gold set of 100+ queries, promote it.

- [ ] **Step 2: Write ADR-0010 — LLM reranking behind a protocol**

Content: a heuristic-only reranker would leave the day's central claim weaker than the industry meaning of the word; Gemini is wired because its key already works and is billed; Claude is written but unverified because §4.6 names it and Levels 2–4 assume it. *Rejected:* a local cross-encoder (2.5 GB, contradicts ADR-0006/0007); a dedicated hosted rerank API (Voyage has no payment method, and Day 7 proved free tiers cannot cover this corpus). **Trigger:** if the LLM reranker does not beat the heuristic by a margin worth its latency and cost, the heuristic becomes the default and the LLM path stays behind the flag.

- [ ] **Step 3: Write `docs/devlog/day-08.md`**

Match the established format (see `day-07.md`). It must contain:

- The comparison table with the **real** numbers from Task 11 Step 6
- The **per-category breakdown**, and an explicit statement of whether the pre-registered prediction held — that hybrid's win comes from identifier queries and it is flat or worse on conceptual ones. Say plainly if it did not.
- Pool coverage, stated as the known limitation of pooling
- The LLM reranker's measured cost from the real run
- That the **Claude backend is written but has never run against the live API**, and that `ANTHROPIC_API_KEY` is still owed
- A **what running it found** section — the execution notes that would have saved time if the plan had known them

- [ ] **Step 4: Update `docs/site/index.html`**

- `DAY 08` everywhere the day number appears
- Pod head `8 shipped · 22 queued`
- Day 7's segment gains `nohead`; Day 8 marked shipped
- Tracker row 08 → SHIPPED
- Level 1 section gains the retrieval comparison table and the per-category breakdown
- ADR-0009 and ADR-0010 cards

- [ ] **Step 5: Run every gate, each on its own line, unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
python -m uv run pytest
```
Expected: four clean exits. **Read each exit code.** Day 4's note said this and Day 7 broke it anyway, shipping nine type errors green.

- [ ] **Step 6: Verify the suite is still hermetic**

```bash
docker compose down
python -m uv run pytest
```
Expected: PASS with Docker stopped. If anything fails here it was reaching the network, and CI will fail on it.

- [ ] **Step 7: Commit**

```bash
git add docs/
git commit -m "docs: ADR-0009, ADR-0010, day-08 devlog, and the retrieval comparison on the running doc"
```

---

## Execution notes (what the plan missed)

Filled in during execution, not before — each entry is something that cost time and
would have been cheap to know. Day 7's ten notes are why several constraints above
exist at all, so this section is load-bearing rather than decorative. Carry the
findings into the Day 9 plan.
