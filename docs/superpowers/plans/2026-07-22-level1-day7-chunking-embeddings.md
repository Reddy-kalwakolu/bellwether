# BELLWETHER Level 1 / Day 7 — Chunking, embeddings, and the embedding desk

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the 67-document corpus into retrievable pieces with structure-aware chunking, embed those pieces through four interchangeable engines — two hosted (Gemini, Voyage), one local and free (Model2Vec/potion), one dependency-free (hashing, for CI) — store the vectors in Qdrant under **named vectors** so every engine's output lives side by side on the same chunk, track token cost per call, and ship a designed console that switches engines and shows what changes.

**Architecture:** Chunking is pure and strategy-driven: `chunkers/` holds one module per strategy (Python AST, Markdown headings, OpenAPI paths, fixed window), each turning one `Document` into `Chunk`s that inherit the document's provenance and add their own (`chunk_index`, `anchor`, `line_span`). `embedders/` defines an `Embedder` protocol plus four implementations behind lazy imports, each reporting a `UsageRecord` (tokens, cost, latency). `vectors.py` wraps Qdrant behind a `VectorStore` protocol, using one collection with a named vector per engine so switching engines is a query-time parameter, not a re-import. `console/` is a FastAPI app serving the desk.

**The load-bearing idea:** *the engine is a parameter, not a commitment.* Every design choice here — the protocol, Qdrant's named vectors, the console — exists so the corpus is embedded once per engine and compared honestly on identical chunks. A comparison where each engine gets its own store, its own chunking, or its own run is not a comparison; it is four anecdotes.

**Tech Stack:** Python 3.11+, Pydantic v2, httpx (already present), Qdrant (Docker), `model2vec` (optional group, numpy-only inference), FastAPI, vanilla HTML/CSS/JS.

## Global Constraints

- Python 3.11+; `mypy --strict` clean on `tests substrate platform bellwether`; ruff + `ruff format --check` clean (line length 100); conventional commits; gates run **unpiped**
- **Tests stay hermetic.** The full suite passes with Docker stopped and **no API keys set**. No test calls Voyage, Gemini, or Qdrant. Hosted engines and the Qdrant store are exercised through injected fakes; the `HashingEmbedder` is the test default
- **A missing API key disables an engine, it never crashes.** `available()` on every engine; the console shows unavailable engines greyed with the reason
- **No new required dependency.** `model2vec` goes in an optional `embeddings` group; Qdrant is reached over HTTP with `httpx`, which is already a dependency — no `qdrant-client`
- Files read/written with explicit `encoding="utf-8"`; content normalised to `\n` before hashing (day-06 constraint, still load-bearing)
- Secrets come from `.env` (gitignored) or the environment. **No key is ever logged, printed, echoed into the console UI, or written into the corpus**
- Cost figures published on the site must be **numbers a real run printed**, never estimates
- The console reuses the running doc's tokens exactly: `--ink #0a0a0d`, `--panel #131318`, `--panel-2 #16161d`, `--line #26262e`, `--text #eaeaef`, `--dim #9a9aa6`, `--signal #e50914`, `--shipped #46d369`; Bricolage Grotesque / Instrument Sans / JetBrains Mono
- Console quality floor: responsive to mobile, visible keyboard focus, `prefers-reduced-motion` respected, no CDN JS beyond the shared Google Fonts link
- Every directory under `tests/` needs an `__init__.py`; `uv` is invoked as `python -m uv`
- Definition of done: `docs/site/index.html` — DAY 07, pod head `7 shipped · 23 queued`, Day 6 segment gains `nohead`, Day 7 shipped, tracker row 07 SHIPPED, Level 1 section extended with the Day 7 comparison; plus `docs/devlog/day-07.md`

## File Structure

| File | Responsibility |
|---|---|
| `bellwether/context/chunking/models.py` | `Chunk`, `ChunkProvenance`, `chunk_id` derivation |
| `bellwether/context/chunking/python_ast.py` | Split Python at module/class/function boundaries |
| `bellwether/context/chunking/markdown.py` | Split Markdown at heading boundaries, carrying the heading path |
| `bellwether/context/chunking/openapi.py` | Split an OpenAPI spec one chunk per path + one for components |
| `bellwether/context/chunking/window.py` | Fixed-window fallback with overlap, and the size-capping used by all strategies |
| `bellwether/context/chunking/router.py` | `chunk_document()` — pick the strategy by `source_type`; `chunk_corpus()` |
| `bellwether/context/chunking/report.py` | The strategy comparison: counts, size distribution, anchor coverage |
| `bellwether/context/embedders/base.py` | `Embedder` protocol, `EmbeddingResult`, `UsageRecord`, registry |
| `bellwether/context/embedders/hashing.py` | Dependency-free deterministic embedder (CI default) |
| `bellwether/context/embedders/model2vec.py` | potion-retrieval-32M, local, lazy import |
| `bellwether/context/embedders/voyage.py` | voyage-3.5 over HTTP, 1024-dim |
| `bellwether/context/embedders/gemini.py` | gemini-embedding-001 over HTTP, 3072-dim |
| `bellwether/context/vectors.py` | `VectorStore` protocol, Qdrant HTTP implementation, in-memory fake |
| `bellwether/context/console/main.py` | FastAPI app: engine list, arm, run comparison, status |
| `bellwether/context/console/static/desk.html` | The embedding desk |
| `docker-compose.yml` | `qdrant` service on 6333 |

---

### Task 1: The chunk and its provenance

**Files:** create `bellwether/context/chunking/__init__.py`, `models.py`; `tests/bellwether/context/chunking/__init__.py`, `test_chunk_models.py`

**Interfaces — produces:**
- `ChunkProvenance`: inherits `doc_id`, `source_path`, `source_type`, `component`, `title` from the parent document, adds `strategy: str`, `chunk_index: int`, `anchor: str | None`, `line_start: int`, `line_end: int`
- `Chunk` (Pydantic): `chunk_id: str`, `doc_id: str`, `text: str`, `content_hash: str`, `provenance: ChunkProvenance`
- `build_chunk(document, text, strategy, chunk_index, anchor, line_start, line_end) -> Chunk`

`chunk_id` is `f"{doc_id}#{chunk_index:04d}"` — still a citation a human can act on, now precise to a region. `anchor` is the semantic handle: a dotted symbol path for code (`Population.member`), a heading path for Markdown (`Decisions › ADR-0005`), a route for OpenAPI (`POST /ad-request`). **A chunk with no anchor is a chunk retrieval cannot explain**, and the comparison report measures exactly that.

Tests: id format; provenance inheritance from the parent document; hash normalisation; anchor optional; line span always covers ≥1 line; empty text rejected.

- [ ] Write failing tests → run → implement → run → commit `feat: the chunk, its anchor, and the provenance it inherits`

---

### Task 2: Four chunking strategies

**Files:** create `python_ast.py`, `markdown.py`, `openapi.py`, `window.py`, `router.py` + one test module each

**Interfaces — produces:** each strategy exposes `split(document: Document) -> list[Chunk]`. `router.chunk_document(document) -> list[Chunk]` dispatches on `source_type`:

| Source type | Strategy | Anchor |
|---|---|---|
| `code` | `python_ast` | dotted symbol path |
| `adr` `devlog` `runbook` `standards` `spec` `plan` `readme` `backlog` | `markdown` | heading path, ` › ` joined |
| `openapi` | `openapi` | `METHOD /path` |
| `config` | `window` | `None` |

- `window.cap(chunks, max_chars=2000, overlap=200)` — every strategy runs its output through this, so a 400-line function or a 900-line heading section still becomes embeddable pieces. Oversized pieces split on the window and **keep the parent anchor with a `part N` suffix**, so context survives the cut.
- `python_ast` walks top-level `FunctionDef`/`AsyncFunctionDef`/`ClassDef`, emits one chunk per symbol with its decorators and docstring, plus one chunk for module-level code (imports, constants). Unparseable files fall back to `window` — a syntax error must not lose a document.
- `markdown` splits on `#`/`##`/`###`, carries the full heading path down, and **never emits a chunk without its heading line** — a fragment that has lost its heading is unciteable.
- `openapi` parses the JSON, emits one chunk per `path × method` containing summary, parameters and response codes, plus one per schema in `components`. Falls back to `window` on unparseable JSON.

Tests per strategy: boundary correctness, anchor correctness, oversized splitting keeps the anchor, malformed input falls back rather than raising, line spans are truthful, and round-tripping a real repo document produces chunks whose concatenated text covers the source.

- [ ] Write failing tests → run → implement → run → commit `feat: structure-aware chunking — Python AST, Markdown headings, OpenAPI paths, windowed fallback`

---

### Task 3: The chunking comparison report

**Files:** create `chunking/report.py`, `tests/.../test_chunk_report.py`

**Interfaces — produces:** `StrategyStats` (chunks, mean/p50/p95/max chars, anchor coverage %, docs covered) and `compare_strategies(documents) -> dict[str, StrategyStats]`, plus `format_comparison(stats) -> str`.

This is Day 7's first published number and it needs no LLM and no network: run every document through **both** its structure-aware strategy and the naive fixed window, and report the difference. The honest metric is **anchor coverage** — the share of chunks that can name what they are. Naive windowing scores near zero by construction; that is the point, and it is measurable rather than asserted.

- [ ] Write failing tests → run → implement → run → commit `feat: chunking strategy comparison — size distribution and anchor coverage`

---

### Task 4: The embedder protocol, cost tracking, and the hashing engine

**Files:** create `embedders/__init__.py`, `base.py`, `hashing.py` + tests

**Interfaces — produces:**
- `UsageRecord`: `engine`, `texts: int`, `tokens: int`, `cost_usd: float`, `latency_ms: float`
- `EmbeddingResult`: `vectors: list[list[float]]`, `usage: UsageRecord`
- `EngineSpec`: `name`, `label`, `dimensions`, `hosted: bool`, `cost_per_million_tokens: float`, `mteb_retrieval: float | None`, `notes: str`
- `Embedder` Protocol: `spec: EngineSpec`, `available() -> tuple[bool, str]`, `embed(texts: list[str]) -> EmbeddingResult`
- `REGISTRY: dict[str, Callable[[], Embedder]]` and `get_embedder(name)`

`HashingEmbedder`: deterministic hashed bag-of-words projected to 256 dims, L2-normalised. Zero dependencies, zero network, stable across platforms. **It is a real lexical embedder, not a stub** — cosine similarity between two chunks sharing vocabulary is genuinely higher — which is what lets the test suite assert retrieval behaviour without a model.

Tests: determinism; same text → same vector; unit norm; shared vocabulary raises cosine above unrelated text; `cost_usd == 0`; usage records tokens; registry lists all four names.

- [ ] Write failing tests → run → implement → run → commit `feat: embedder protocol with per-call cost tracking, and the dependency-free hashing engine`

---

### Task 5: The three real engines

**Files:** create `embedders/model2vec.py`, `voyage.py`, `gemini.py` + tests; modify `pyproject.toml` (optional `embeddings` group)

**Interfaces:** each implements `Embedder`.

- `Model2VecEmbedder` — `minishlab/potion-retrieval-32M`, 512-dim, lazy `import model2vec` inside `available()`/`embed()`. Missing package → `available() == (False, "pip install --group embeddings")`. Cost 0.
- `VoyageEmbedder` — `POST https://api.voyageai.com/v1/embeddings`, model `voyage-3.5`, 1024-dim, `$0.06/M tokens`, key from `VOYAGE_API_KEY`. Batches of 128. Reads `usage.total_tokens` from the response for **real** cost, never an estimate.
- `GeminiEmbedder` — `POST .../v1beta/models/gemini-embedding-001:batchEmbedContents`, 3072-dim, key from `GEMINI_API_KEY`.

Both hosted engines take an injectable `transport` (an `httpx.Client`-shaped callable) so tests exercise request-building, batching, error mapping and usage parsing **against a fake, never the network** — the same seam Day 3 used for `CampaignClient`.

Tests: unavailable without a key, with the reason; request body and headers correct; batching splits at the limit; usage parsed from the response; cost computed from real tokens; non-200 raises a typed `EmbeddingError` naming the engine; **no test performs real I/O**.

- [ ] Write failing tests → run → implement → run → commit `feat: Gemini, Voyage, and local potion embedders behind one protocol`

---

### Task 6: Qdrant behind a vector-store protocol

**Files:** create `bellwether/context/vectors.py` + tests; modify `docker-compose.yml`

**Interfaces — produces:**
- `VectorStore` Protocol: `ensure_collection(specs)`, `upsert(chunks, engine, vectors)`, `search(engine, vector, limit, source_types=None) -> list[SearchHit]`, `stats() -> CollectionStats`
- `QdrantVectorStore(base_url, transport)` — HTTP only, no `qdrant-client`
- `InMemoryVectorStore` — cosine over a dict, the test default and a legitimate fallback when Qdrant is down

**Named vectors are the reason Qdrant replaces ChromaDB.** One collection, one point per chunk, and a named vector per engine (`gemini`, `voyage`, `potion`, `hashing`) on that same point. Switching engines becomes a `using=` parameter on the query rather than a different collection, a different import, or a different chunking run — which is the only way the comparison is fair. Chroma has no clean equivalent.

`docker-compose.yml` gains `qdrant` (image `qdrant/qdrant`, host port **6333**, named volume, healthcheck) — a peer of the substrate services, not inside Postgres, keeping the AI foundation's storage separate from the ads platform's.

Tests: collection creation is idempotent and declares one named vector per engine; upsert builds the right payload; search passes `using` and filters; a hit carries chunk provenance back; in-memory and Qdrant implementations satisfy the same protocol tests; Qdrant tests use a fake transport.

- [ ] Write failing tests → run → implement → run → commit `feat: Qdrant vector store with one named vector per engine, behind a protocol`

---

### Task 7: Wire the pipeline through and run it for real

**Files:** modify `bellwether/context/pipeline.py`, `__main__.py`; create `bellwether/context/embedding_run.py` + tests

**Interfaces — produces:** `EmbeddingRun` (engine, chunks, tokens, cost_usd, wall_ms, dimensions) and `embed_corpus(chunks, embedder, store) -> EmbeddingRun`; CLI gains `--chunk`, `--embed ENGINE`, `--engines all`.

Then **run it**: chunk the 67 documents, embed with all four engines, store in Qdrant, and record the real numbers. Every published figure on the site comes from this run's output.

- [ ] Implement → run `python -m bellwether.context --engines all` → record output → commit `feat: chunk and embed the corpus across every available engine`

---

### Task 8: The embedding desk

**Files:** create `bellwether/context/console/main.py`, `static/desk.html` + tests

**API:** `GET /` (the desk), `GET /api/engines`, `GET /api/status`, `POST /api/arm {engine}`, `POST /api/compare`, `GET /health`.

**Design direction — the patch bay.** The running doc is a broadcast build log: a blinking REC dot, `DAY 07 / 30`, `SEQ` section markers, a 30-day timeline strip. The desk extends that world rather than inventing a second one. Four vertical **channel strips**, one per engine, sharing one source (the chunked corpus). Exactly one is *armed*.

**Signature element:** the armed strip — a solid `--signal` rail down its left edge, the strip lifted a few pixels and its type at full `--text`, while unarmed strips recede to `--dim` on `--panel-2` with the rail dark. Arming a different engine moves the rail and re-animates every meter in one staggered sweep. That is the page's only orchestrated motion, and it is disabled under `prefers-reduced-motion`.

**Colour discipline:** monochrome plus red for *armed only*, plus `--shipped` green used for exactly one thing — the **winning value in each comparison row**. Because the winners differ per row (Gemini takes quality, hashing takes speed, potion and hashing tie on cost), the green scatters across strips, and the central finding of the day becomes legible at a glance instead of needing a caption.

**Type:** Bricolage Grotesque 800 uppercase for engine names, JetBrains Mono for every number and label — this is a data desk and numbers are the content — Instrument Sans for the two prose lines.

**No numbered markers.** The engines are alternatives, not a sequence; numbering them would encode an order that does not exist.

```
┌────────────────────────────────────────────────────────────────┐
│ ●LIVE  BELLWETHER · EMBEDDING DESK       CORPUS 67 · 1,9xx CH  │
├────────────────────────────────────────────────────────────────┤
│ CHUNKING   ast ▸ n · markdown ▸ n · openapi ▸ n · window ▸ n   │
│ ▁▂▅█▇▅▃▂▁  size distribution                anchor coverage n% │
├───────────────┬───────────────┬──────────────┬────────────────┤
│▐ GEMINI       │  VOYAGE       │  POTION      │  HASHING       │
│▐ ARMED        │               │              │                │
│▐ 3072   dims  │ 1024   dims   │  512   dims  │  256   dims    │
│▐ 68.32  mteb ✓│ 67.x   mteb   │ 35.06  mteb  │   —    mteb    │
│▐ $0.0xxx cost │ $0.0xxx cost  │ free   ✓     │ free   ✓       │
│▐ xx.x s       │ xx.x s        │  x.x s       │ 0.0x s ✓       │
│▐ ████████     │ ███████       │ ████         │ ██             │
│▐ [ ARMED ]    │ [ ARM ]       │ [ ARM ]      │ [ ARM ]        │
└───────────────┴───────────────┴──────────────┴────────────────┘
│ RUN COMPARISON →      last run --:--:-- · $0.0xxx · n engines  │
└────────────────────────────────────────────────────────────────┘
```

Unavailable engines render greyed with the reason in place of the arm button (`no GEMINI_API_KEY`), never as an error.

Tests: engine list shape; arming an unknown engine is a typed 404; arming an unavailable engine is a typed 409 naming the reason; status reports corpus and armed engine; the HTML declares the shared tokens and contains no key; `/health`.

- [ ] Write failing tests → run → implement → run → commit `feat: the embedding desk — arm an engine, run the comparison, read the cost`

---

### Task 9: Decisions, docs, and definition of done

**Files:** create `docs/adr/0007-hosted-embeddings-with-a-local-fallback.md`, `docs/adr/0008-qdrant-over-chromadb.md`, `docs/devlog/day-07.md`; modify `docs/site/index.html`, `pyproject.toml`

- **ADR-0007** — the spec said local sentence-transformers by default; the corpus is ~140k tokens, so the best hosted model costs under a cent to embed, runs on the provider's hardware, and leaves a 16 GB laptop free for Docker. Quality-first turned out to be the *lighter* option, which inverted the original reasoning. Local potion stays as the free-iteration tier, hashing as the CI tier. Rejected: sentence-transformers + torch (2.5 GB in CI, network in tests, contradicts ADR-0006); Qwen3-Embedding-8B (best open score, unusable on this hardware); FastEmbed (its own tracker shows it slower than what it replaces). **Trigger:** corpus growth past a few million tokens, where per-run cost stops being a rounding error.
- **ADR-0008** — Qdrant over the spec's ChromaDB, on named vectors: one point per chunk carrying every engine's vector makes engine choice a query parameter and the comparison fair by construction. Also Rust-light on RAM, real payload filtering for `source_type`/`component` facets, and no forced migration later. Rejected: Chroma (no clean named-vector story, prototype tier); pgvector (would put AI storage inside the ads platform's Postgres, blurring substrate and foundation); LanceDB (younger, weaker concurrent access). **Trigger:** if filtering and scale stay this modest through Level 3, revisit whether the in-memory store is enough.
- Site: DAY 07 everywhere, pod head `7 shipped · 23 queued`, Day 6 `nohead`, Day 7 shipped, tracker row 07 SHIPPED, Level 1 section gains the Day 7 comparison table and the desk, ADR-0007/0008 cards.
- Devlog `day-07.md` in the established format, including **what running it found**.

- [ ] Run every gate unpiped → commit `docs: ADR-0007, ADR-0008, day-07 devlog, embedding comparison on the running doc`

---

## Execution notes (what the plan missed)

Recorded during execution, so the next plan does not repeat these:

1. **`PUT /points` in Qdrant replaces a point outright.** The plan's two-call write (payload first, named vector second) destroys every previously written engine's vector, because the payload write is itself a full replace. Every unit test passed against the fake. Found only by pointing the store at a running Qdrant and asking whether the first engine's vector survived the second engine's write — it did not. Correct sequence: create new points with payload *and* vector together; extend existing points through `PUT /points/vectors`, which merges. **Any plan that fakes a datastore must include one live round-trip before the design is trusted.**
2. **`PUT /points/vectors` cannot create a point**, so there is no single non-destructive call that works for both cases. The write path has to branch on existence, which means an existence probe per batch. Cheap, but the plan assumed a uniform path that does not exist.
3. **Per-engine counts must be measured, not read off the collection config.** A *declared* named vector and a *populated* one are different things; reporting the configured names made an engine with zero vectors show a full count. `has_vector` filters give the truth.
4. **PowerShell's `-Encoding utf8` writes a BOM.** The first line of `.env` parsed as `﻿VOYAGE_API_KEY`, presenting as a missing credential while the file plainly held one. Read `.env` as `utf-8-sig`. This is Day 6's line-ending trap wearing a different hat: **on Windows, assume every text file you did not write with Python has an encoding surprise in line 1.**
5. **Adding retry made an existing test sleep for three real minutes.** A test asserting Gemini's 429 handling inherited a 30-second backoff six times over. Anything that waits in production takes an injected clock, and the test must pass it.
6. **Free tiers cannot embed this corpus.** Gemini allows 100 embed requests per *day* free and one pass needs 585; Voyage throttles below the required token rate without a payment method. The retry logic is correct and both still failed, because this is a billing ceiling. **A plan that depends on a hosted model must state the tier it assumes.**
7. **The gate chain hid a failure again.** `mypy ... | tail -2` exits with `tail`'s status, so nine type errors were committed green — day-04's execution note 6, broken by the author of that note. Run each gate on its own line, unpiped, and read the exit code.
8. **Empty `__init__.py` files produce zero chunks**, so the chunk-level document count (65) is legitimately lower than the corpus count (67). Not a bug — an empty file has nothing to retrieve — but any test asserting equality between the two will fail.
9. **Windows console output mangles `›`.** The anchor separator prints as `?` under cp1252 stdout. The data is correct; only the terminal is lying. Redirect to a file or set `PYTHONIOENCODING=utf-8` before reading numbers off the screen for a video.
10. **The plan was about 1.5 days of work, not one.** Tasks 1–7 and 9 landed; Task 8, the console, did not. Sizing a day that contains four integrations *and* a designed UI was optimistic, and the honest split was visible in the file table before a line was written.
