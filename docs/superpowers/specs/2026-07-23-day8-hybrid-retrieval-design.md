# BELLWETHER Level 1 / Day 8 — Hybrid retrieval, reranking, and an honest number

**Date:** 2026-07-23
**Status:** Approved design, pending implementation plan
**Level:** 1 — Context Layer (Days 6–10)
**Depends on:** Day 6 (ingestion, 67 documents), Day 7 (chunking + embeddings, 585 chunks in Qdrant)

---

## 1. What this day is for

Day 7 can find things that *mean* what you asked. It cannot find things that are *named* what you
asked. Embedding `budget_micros` produces a vector that is close to "budget", "spending", "cost" —
and no closer to the one chunk that actually defines the field than to twenty chunks that discuss
money in general. An engineer searching a codebase asks both kinds of question, often in the same
sentence, and a context layer that only answers one of them will ground agents badly for the rest
of the build.

So Day 8 adds the lexical half, fuses the two, reranks the result, and — the part that matters —
**measures whether any of that actually helped**, against ground truth built so that no
configuration can win by having defined the answer key.

**The load-bearing idea:** *a retrieval system that has not been measured against an adversarial
answer key is a claim, not a result.* Every design decision below exists to keep the day's
published number honest, including the decisions that make it harder to produce.

---

## 2. Scope

**In:**

- BM25 lexical retrieval over the existing 585 chunks
- Reciprocal Rank Fusion of the lexical and vector result sets
- A `Reranker` protocol with two implementations — free/deterministic, and LLM-backed
- A thin LLM client (the first piece of spec §4.6), Gemini backend wired, Claude backend owed
- A gold query set, a pooled judgement harness, and nDCG@10 / recall@10 / MRR
- The CLI wiring Day 7 promised and did not deliver

**Out:**

- **The embedding desk console.** Day 7's Task 8 did not land; Day 7's own execution note 10
  records that a day containing four integrations *and* a designed UI was mis-sized. Repeating that
  shape on a harder day would repeat the outcome. The console moves to **Day 9**, where it absorbs
  the AST knowledge graph's visualisation too — one design day, two features to show.
- **Voyage.** Still blocked on a payment method (Day 7 execution note 6). Three of four engines are
  populated; the eval runs on what exists and says so.

**Sizing, stated up front.** This is still eight components on a day that also pays Day 7's CLI
debt, and the honest read of Day 7's execution note 10 is that I am bad at this estimate. The
riskiest item is not the code — BM25 and RRF are small and well understood — it is §6, where
building and judging the gold set is unbounded work that no test can tell me is finished. If the day
has to shed something, it sheds the **LLM reranker** (§5.4), not the evaluation: hybrid retrieval
measured honestly is a complete day, and reranking measured badly is not a day at all.

---

## 3. What already exists, and what this day may not break

Day 8 is the first day that consumes Day 7's interfaces rather than defining its own. They are
fixed points:

| Interface | Where | Day 8's use |
|---|---|---|
| `Chunk`, `ChunkProvenance` | `chunking/models.py` | The unit BM25 indexes and the reranker reorders |
| `SearchHit` | `vectors.py` | The shape every retrieval path returns, lexical included |
| `VectorStore` protocol | `vectors.py` | Dense retrieval, unchanged; `InMemoryVectorStore` stays the test default |
| `Embedder` protocol, `EngineSpec` | `embedders/base.py` | Query embedding; `HashingEmbedder` stays the CI default |
| `UsageRecord`, `cost_for` | `embedders/base.py` | Reused verbatim by the LLM client — one cost vocabulary, not two |
| `HttpPost` protocol | `embedders/base.py` | The seam the LLM backend is tested through |

**Constraint:** no change to `Chunk`, `SearchHit`, or the `VectorStore` protocol. A retrieval layer
that has to reshape its store's return type has the wrong boundary. If Day 8 finds it needs a field
that `SearchHit` lacks, that is a finding to record, not a licence to widen a Day 7 interface late
on a Wednesday.

---

## 4. Architecture

```
bellwether/
  llm/
    base.py          LLMClient protocol, LLMResponse, LLMError, REGISTRY
    gemini.py        Gemini backend over HTTP, injected transport      [wired]
    claude.py        Claude backend                                     [owed — see §8]
  context/
    retrieval/
      tokenize.py    The identifier-aware tokenizer
      bm25.py        Okapi BM25 index over Chunks
      fusion.py      Reciprocal Rank Fusion + weighted-score alternative
      search.py      The orchestrator — one entry point, five configurations
      rerank/
        base.py      Reranker protocol, RerankResult
        heuristic.py Dependency-free, deterministic. The CI default
        llm.py       LLM reranker over bellwether.llm, structured output
  eval/
    gold.py          GoldQuery, GoldSet, load/validate
    pooling.py       Build the judgement pool from N configurations
    metrics.py       nDCG@10, recall@10, MRR, per-category breakdown
    report.py        The comparison table Day 8 publishes
data/
  gold/day08-retrieval.json   The committed answer key
```

Retrieval is a pipeline of pure, separately testable stages — `retrieve → fuse → rerank` — each
taking and returning `list[SearchHit]`. Nothing in the chain knows which engine produced the dense
side or which backend reranked; every one of those is a parameter, which is what makes the
comparison in §7 possible at all.

---

## 5. The components

### 5.1 The tokenizer — the whole reason hybrid works

`tokenize.py` is small and load-bearing. For every token it emits **both** the original and its
parts:

```
budget_micros        → ["budget_micros", "budget", "micros"]
AdDecisionService    → ["addecisionservice", "ad", "decision", "service"]
POST /ad-request     → ["post", "ad-request", "ad", "request"]
```

Keeping the original is what lets an exact-identifier query score exactly; emitting the parts is
what lets a half-remembered one still match. Dropping either half collapses hybrid back into one of
the two things it is meant to beat.

Lowercased, punctuation-split, a small stopword list, no stemming — stemming a corpus that is
substantially source code costs more than it returns.

### 5.2 BM25

Standard Okapi BM25, `k1=1.5`, `b=0.75`, pure Python over `dict`s. No `rank-bm25` dependency: the
scoring function is fifteen lines, and a dependency whose source is shorter than its changelog is a
liability, not a convenience. Built from the same `list[Chunk]` the vector path embeds — identical
inputs, which is the same discipline ADR-0008 imposed on the four engines.

Returns `SearchHit` with `score` set to the BM25 score. Scores are **not** normalised; §5.3 explains
why they must not be.

### 5.3 Fusion

**Reciprocal Rank Fusion**, `k=60`:

```
score(chunk) = Σ  1 / (k + rank_in_list_i)
```

RRF uses only rank position, never the underlying score. This is the point. Cosine similarity lives
in [-1, 1] and BM25 is unbounded; any scheme that adds them must first normalise them, and every
normalisation is a tuning knob that can be turned until the preferred system wins. RRF has one
constant and it is the published default.

A `WeightedFusion` alternative (min-max normalise, then `α·dense + (1-α)·lexical`) ships alongside
so the choice is *measured* rather than asserted — it appears in the §7 table as its own row, and
if it wins, it wins.

### 5.4 The rerankers

```python
class Reranker(Protocol):
    @property
    def spec(self) -> RerankerSpec: ...
    def available(self) -> tuple[bool, str]: ...
    def rerank(self, query: str, hits: Sequence[SearchHit], limit: int) -> RerankResult: ...
```

Deliberately identical in shape to Day 7's `Embedder` — `spec` / `available()` / one verb — because
the lesson generalises: *the engine is a parameter, not a commitment*. A missing key disables a
reranker with a reason a human can act on; it never crashes.

**`HeuristicReranker`** — free, deterministic, zero network, the CI default. Boosts on exact
identifier match in `text`, anchor-path match, source-type prior (an ADR outranks a devlog for a
"why" question), and a length penalty for chunks so long the match is incidental. It is a real
reranker with a defensible feature set, not a stub, which is what lets the test suite assert rerank
*behaviour* without a model.

**`LLMReranker`** — scores candidates through the LLM client with a structured-output schema
(`[{chunk_id, relevance: 0|1|2}]`), batched, top-20 by default. Structured output rather than parsed
prose: a malformed ranking becomes impossible rather than merely unlikely. A failure returns a typed
error naming the backend, and the caller falls back to the fused order rather than to nothing.

### 5.5 The LLM client

The first piece of spec §4.6, kept deliberately thin — a provider protocol, an injected transport,
structured output, and a `UsageRecord` per call. Not a framework; a seam.

```python
class LLMClient(Protocol):
    @property
    def spec(self) -> ModelSpec: ...
    def available(self) -> tuple[bool, str]: ...
    def complete(self, prompt: str, schema: dict, max_tokens: int) -> LLMResponse: ...
```

**Gemini ships wired.** The `GEMINI_API_KEY` already works and is billed — Day 7's run spent
$0.0223 through it — so the day needs no new credential to produce a real number.

**Claude is owed, and the code says so.** Spec §4.6 names Claude API (Haiku-class for dev) plus
Ollama, and Levels 2–4 — code-gen, test-gen, PR pre-review, ops agents — all assume it. Shipping
only Gemini would leave the entire AI layer on a provider Level 2 does not use. The registry carries
a `claude` entry that reports `available() == (False, "no ANTHROPIC_API_KEY")`, so the gap is
visible in the tooling rather than buried in a document. Model `claude-haiku-4-5`, $1.00/M input,
$5.00/M output.

---

## 6. Evaluation — the part that decides whether any of this is true

### 6.1 Why the obvious approaches are all rigged

| Approach | Why it is not usable |
|---|---|
| Label by inspecting what the retriever returns | Rewards whatever was built. Circular. |
| Derive queries from chunk text or anchors | Anchors are literal substrings of their chunks, so BM25 scores near-perfectly by construction. The hybrid-vs-vector comparison is decided before it runs. |
| Judge only the hybrid system's results | Vector-only can never get credit for a chunk hybrid missed. Guarantees the advocated system wins. |

### 6.2 Pooled judgement

The standard IR method, and the only one here that survives scrutiny:

1. **Write ~30 queries and commit them before any retrieval runs.** Ordering is the control: a
   query set committed after seeing results is a query set tuned to results, and git records which
   happened.
2. **Retrieve top-10 from all five configurations in §7**, not just the three retrieval ones. This
   matters and is easy to get wrong: reranking reorders a *window* (hybrid's top-20), so a chunk
   ranked 14th by hybrid can be promoted into a reranked top-10 while sitting outside a pool built
   only from hybrid's top-10. It would then go unjudged and score zero — silently penalising the
   reranker for working. Pool every configuration that produces a ranking.
3. **Pool the union, shuffle it, strip provenance.** The judge sees a query and a chunk, never which
   system surfaced it.
4. **Grade 0 / 1 / 2** — irrelevant, partially answers, fully answers. Graded rather than binary
   because nDCG needs it and because "partially" is the honest verdict on a lot of real hits.
5. **Report pool coverage.** A chunk no configuration retrieved was never judged, and a relevant
   chunk outside the pool counts against nobody. This is a real limitation of pooling, TREC states
   it, and so will the devlog.

### 6.3 Query categories

Every query is tagged, and results break out by tag:

| Category | Example | Expectation |
|---|---|---|
| `identifier` | "where is `budget_micros` enforced" | BM25 should dominate; vector should be poor |
| `conceptual` | "why did we choose Qdrant over Chroma" | Vector should dominate |
| `cross_document` | "what breaks when a campaign's budget overflows 32 bits" | Both weak alone; fusion's real case |

**This breakdown is the finding, not a footnote.** My prior is that hybrid's entire aggregate win
comes from `identifier`, and that on `conceptual` it is flat or slightly worse than vector alone. If
that is what the numbers say, that is what gets published — a headline "hybrid beats vector by N%"
that hides a category where it lost is the kind of result this project exists to not produce.

### 6.4 Metrics

**nDCG@10** (primary — graded, rank-aware), **recall@10** (did the answer make the cut at all),
**MRR** (how far a human scrolls). Reported per configuration and per category, with the
per-query spread, because a mean over 30 queries hides a lot.

---

## 7. What the day publishes

One table, five rows, real numbers:

| Configuration | nDCG@10 | recall@10 | MRR | latency p50 |
|---|---|---|---|---|
| BM25 only | | | | |
| Vector only (gemini) | | | | |
| RRF hybrid | | | | |
| Hybrid + heuristic rerank | | | | |
| Hybrid + LLM rerank (gemini) | | | | |

Plus the same table split three ways by category, and the LLM reranker's measured token cost from
an actual run — never an estimate (the Day 7 constraint, still binding).

**A negative result ships.** If reranking does not move nDCG, or if the LLM reranker does not beat
the free heuristic, that is the day's finding and it goes on the site with the same prominence a
win would get. A day that can only publish good news is not measuring anything.

---

## 8. Decisions and their reversal conditions

**ADR-0009 — Hybrid retrieval with RRF over score-weighted fusion.** RRF uses rank alone, so it
needs no normalisation and has no knob to tune toward a preferred outcome. Weighted fusion ships as
a measured alternative rather than a rejected one. *Rejected:* score normalisation as the default
(every scheme is a tuning knob); learned fusion weights (nothing to train on at 30 queries, and
fitting weights on the eval set is the same circularity §6.1 rejects). **Trigger:** if weighted
fusion beats RRF across all three categories on a gold set of 100+ queries, promote it.

**ADR-0010 — An LLM reranker behind a protocol, Gemini first, Claude owed.** Reranking with a
heuristic alone would leave the day's central claim weaker than the industry meaning of the word.
Gemini is wired because its key already works and is billed. Claude is owed because §4.6 names it
and Levels 2–4 assume it. *Rejected:* a local cross-encoder (sentence-transformers pulls 2.5 GB and
contradicts ADR-0006/0007); a dedicated hosted rerank API (Voyage has no payment method, and Day 7
proved free tiers cannot cover this corpus). **Trigger:** if the LLM reranker does not beat the
heuristic by a margin worth its latency and cost, the heuristic becomes the default and the LLM path
stays available behind the flag.

---

## 9. Constraints

- Python 3.11+; `mypy --strict` clean on `tests substrate platform bellwether`; ruff + `ruff format
  --check` clean (line length 100); conventional commits
- **Gates run unpiped, each on its own line.** Broken on Day 4, broken again on Day 7 by the author
  of the Day 4 note. A piped gate exits with `tail`'s status and commits red as green.
- **Tests stay hermetic.** The suite passes with Docker stopped and no API keys set. No test calls
  Gemini, Qdrant, or the network. `HashingEmbedder` + `InMemoryVectorStore` + fake transports.
- **No new required dependency.** BM25 is hand-written; the LLM client uses `httpx`, already present.
- A missing API key disables a backend with a reason; it never crashes.
- Files read/written `encoding="utf-8"`; `.env` read as `utf-8-sig` (Day 7 execution note 2 — the
  BOM that presented as a missing credential)
- Anything that waits in production takes an injected clock (Day 7 execution note 4 — the test that
  slept for three real minutes)
- Published cost figures come from a real run's output
- Every directory under `tests/` needs `__init__.py`; `uv` runs as `python -m uv`
- **Before trusting the design, one live round-trip.** Day 7 note 1: every unit test passed against
  a fake Qdrant while the real one silently destroyed data. The gold set gets built against a
  running Qdrant, not a fixture.

---

## 10. Definition of done

- `docs/adr/0009-hybrid-retrieval-with-rrf.md`, `docs/adr/0010-llm-reranking-behind-a-protocol.md`
- `docs/devlog/day-08.md` in the established format, including **what running it found**
- `docs/site/index.html`: DAY 08, pod head `8 shipped · 22 queued`, Day 7 segment gains `nohead`,
  Day 8 shipped, tracker row 08 SHIPPED, Level 1 section extended with the retrieval comparison
- `data/gold/day08-retrieval.json` committed, with its judgements
- CLI: `--chunk`, `--embed ENGINE`, `--engines all` (Day 7's debt), plus `--search QUERY`, `--eval`
- Every gate run unpiped and green
