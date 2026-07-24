# Day 8 — Hybrid retrieval, reranking, and a prediction that was wrong

**Level:** 1 · **Date:** 2026-07-23

## Shipped
- **BM25 over the 708-chunk corpus**, hand-written — the scoring function is fifteen lines, shorter than the changelog of the package that would have supplied it
- **An identifier-aware tokenizer**, the reason hybrid works at all: every token is emitted *whole and split*, so `budget_micros` yields `budget_micros`, `budget`, `micros`. The whole form is what an exact query matches; the parts are what a half-remembered one matches
- **Reciprocal Rank Fusion** over the lexical and vector rankings, plus a weighted-score alternative kept as a *measured* option rather than a rejected one — it gets its own row
- **A `Reranker` protocol with two implementations** — a free, deterministic, offline one (identifier match, anchor match, source-type prior, length penalty) and an LLM one. Shaped exactly like Day 7's `Embedder`, because the lesson generalises: the engine is a parameter, not a commitment
- **The LLM provider seam** (spec §4.6's first piece): a protocol, an injected transport, structured output, and a `UsageRecord` per call, reusing Day 7's cost vocabulary rather than inventing a second one. **Gemini wired; Claude written and hermetically tested but never run** — what is owed is the credential, not the code
- **A pooled-judgement evaluation harness**: 26 queries committed *before* any retrieval ran, 475 query–chunk pairs graded 0/1/2 with provenance stripped, nDCG@10 / recall@10 / MRR, broken out by question shape
- **Day 7's unpaid CLI debt, paid.** `embed_corpus()` existed and *nothing called it* — Day 7's published numbers came from an ad-hoc script nobody reading the repo could reproduce. `--chunk`, `--embed`, `--engines all`, plus `--search` and `--eval`, are now commands
- ADR-0009 and ADR-0010; 409 tests (41 new) passing with Docker stopped and no API keys set; ruff, ruff format, mypy strict clean

## The number, and the prediction it broke

The design spec pre-registered a prediction, in writing, before a line of retrieval code ran: *hybrid's aggregate win would come almost entirely from identifier queries, and on conceptual queries it would be flat or slightly worse than vector alone.*

The premise was wrong. **There is no aggregate hybrid win to explain.**

| Configuration | nDCG@10 | recall@10 | MRR | p50 |
|---|---|---|---|---|
| **dense (gemini)** | **0.670** | **0.757** | 0.774 | 812 ms |
| hybrid + LLM rerank | 0.656 | 0.682 | **0.870** | 9,309 ms |
| hybrid + heuristic rerank | 0.639 | 0.689 | 0.791 | 783 ms |
| hybrid (RRF) | 0.628 | 0.674 | 0.844 | 842 ms |
| hybrid (weighted) | 0.624 | 0.678 | 0.834 | 802 ms |
| lexical (BM25) | 0.395 | 0.496 | 0.556 | **2.9 ms** |

Vector search alone beat every hybrid configuration. Adding the lexical side *lowered* nDCG.

By question shape, where the prediction was most specific:

| Configuration | identifier | conceptual | cross-document |
|---|---|---|---|
| dense (gemini) | **0.711** | 0.598 | **0.721** |
| hybrid (RRF) | 0.696 | 0.583 | 0.589 |
| hybrid + heuristic | 0.652 | 0.601 | 0.681 |
| hybrid + LLM | 0.696 | 0.583 | 0.711 |
| hybrid (weighted) | 0.661 | **0.620** | 0.569 |
| lexical (BM25) | 0.426 | 0.452 | 0.250 |

The prediction said BM25 should *dominate* the identifier category — questions like *where is `budget_micros` enforced*. It came last there, at 0.426 against dense's 0.711. Gemini's embeddings are good enough on this corpus that they win the category that was supposed to be lexical search's home ground.

Pool coverage: **100%** on the first run, 99.9% on the second. Every chunk any configuration retrieved was in the judged pool, so no configuration lost points to a chunk nobody graded.

**What I would have published without the eval:** "hybrid retrieval, with reranking." It would have been slower, more complex, and worse — and it would have looked like progress.

## Decisions
- **ADR-0009: RRF over weighted fusion.** Cosine similarity lives in [-1, 1]; BM25 is unbounded and reaches 20. Adding them requires normalising first, and every normalisation is a knob that can be turned until the preferred system wins. RRF uses rank position only and has one published constant. The weighted alternative shipped as a measured row — and on conceptual queries it was actually the best fusion (0.620), which is why it stays.
- **ADR-0010: the LLM reranker lives behind a protocol, and it is not the default.** It works, it is the best hybrid row, and it holds the best MRR overall (0.870 — the first useful answer is nearly always rank 1). It also costs **9.3 seconds per query against 783 ms** for the free heuristic, and still loses to plain dense search. The heuristic stays the default; the LLM path stays behind a flag.
- **Nothing beats dense on this corpus — and that is a finding about *this* corpus.** 708 chunks of well-structured ADRs, devlogs and typed Python, embedded by a frontier model. A larger, messier, more jargon-dense corpus is exactly where BM25 earns its keep. The number is a baseline, not a law.
- **The engine is still a parameter.** Six configurations, one code path, one command. Any future change is now an experiment against a fixed answer key rather than an opinion.

## What running it found
1. **The LLM reranker had never once run, and the score was what exposed it.** `hybrid-llm` came back byte-identical to `hybrid` — same nDCG, recall and MRR to three decimals, across every category. That is impossible if a reranker is doing anything, so it wasn't: every Gemini call was failing and the reranker was silently degrading to the fused order, exactly as designed to. Three stacked causes, none visible to a hermetic test: **(a)** `RANKING_SCHEMA` graded relevance with an integer `enum`, and Gemini's `responseSchema` is a restricted OpenAPI dialect that rejects integer enums outright (400, "TYPE_STRING"); **(b)** the default model `gemini-2.5-flash` now returns **404 — "no longer available to new users;"** **(c)** with (a) and (b) fixed the calls finally reached the model and *still* degraded — the 2,048-token default truncated the JSON array mid-value on 25 of 26 queries (twenty full-path `chunk_id`s past a model that spends output budget thinking runs to ~5,000 tokens), and a reply that *starts* like JSON and stops mid-array raises exactly like malformed JSON, so it fell back to the fused order as quietly as the first two did. Raised to 8,192. A fake transport can validate neither a provider's schema dialect, its model catalogue, nor how long its answers run. This is Day 7's execution note 1 in a new place: **anything faked in tests needs one live round-trip before it is trusted.**
2. **A hardcoded model id rotted in under a day of use.** Spec §4.6 warned that stale model names in a portfolio repo signal copy-paste planning; it did not occur to me that the name would go stale *between writing the plan and running it*. The default is now the `gemini-flash-latest` alias. A pinned snapshot is a decision to re-check, not a default.
3. **A silent degrade path is a correctness feature and an observability hole.** Falling back to the fused order was the right behaviour — an outage should cost you a slightly worse ranking, not an empty result. But it meant a totally broken reranker produced *plausible numbers*. The only thing that caught it was two rows being suspiciously identical. Degrade paths need to be loud somewhere, even when they are quiet in the response — so the eval now wraps the reranker in a counter and prints `N/26 queries degraded to the fused order` on every run, zero or not. That is the loud channel the response deliberately lacks.
4. **My own plan could not pass its own tests, twice.** The heuristic reranker's rank decay put a 0.5 gap between ranks 1 and 2 — equal to the largest feature weight — so three of its ten tests were arithmetically unsatisfiable. Separately, the identifier rule counted any word of eight characters or more, so "frequency" and "observability" collected the largest boost in the table on ordinary prose, inflating the very baseline the LLM is measured against. Both found in review, both fixed before the number was published.
5. **An unanchored `sed` corrupted a different task's expected test count.** Fixing one number in the plan rewrote every matching line in the file. Caught by the next reviewer. Anchor the line, or read the diff.
6. **Two Day 6 tests hard-coded the corpus's exact composition** (`spec == 1`) in a repo that gains a spec every few days. Committing the Day 8 design doc broke them. The same test already used `>= 5` for ADRs and devlogs — `spec` was simply missed.
7. **`.gitignore` had a bare `data/`, which would have made the answer key untracked.** Git does not descend into a directory ignored by a bare rule, so a `!data/gold/` negation alone does nothing — the parent must become `data/*`. A published number whose answer key is not in the repo is a number nobody can check.
8. **nDCG could exceed 1.0.** A duplicated chunk in a ranking counted its gain twice while the ideal ranking counted it once. Fusion dedupes, so it could not fire in practice — but a metric that can break its own [0, 1] invariant is a latent corruption in the headline table.

## Where this goes next
The 0.670 is a **baseline, not a ceiling**, and the point of today is that every idea below is now testable against a fixed answer key instead of argued about:

1. **A purpose-built reranker** (Cohere or Voyage `rerank`, or a local cross-encoder). Usually the single largest jump in retrieval quality, and ADR-0010 already names it as the reversal trigger. Our LLM reranker is a general model doing a specialist's job, slowly.
2. **Contextual chunks** — prepend a one-line "from ADR-0008, about Qdrant" header to each chunk before embedding, so a chunk cut out of its document does not lose what it was about.
3. **A larger, second-opinion gold set.** 26 queries graded once by one person is thin, and the grading was strict. More queries and a second grader would both move the number and make it trustworthy.
4. **Query rewriting** for the conceptual and cross-document shapes, which scored lowest — expand the question, or embed a hypothetical answer rather than the question itself.

## For the video
1. Read the pre-registered prediction off the design spec, in git, timestamped **before** the retrieval code existed. Then the table. **I was wrong**
2. `--search "where is budget_micros enforced" --mode lexical` then `--mode dense`. The identifier query BM25 was supposed to own — and dense wins it
3. The comparison table, all six rows, one command
4. The per-category split. A headline that hid the categories would have hidden the whole story
5. The honest part: two rows identical to three decimals, and what that turned out to mean — a reranker that had never run, behind a degrade path working exactly as designed
6. The 9.3 s versus 783 ms line. The LLM reranker is the best hybrid row *and* it is not the default, and say why
7. `data/gold/day08-retrieval.json` on screen: 475 graded pairs, committed, checkable by anyone

## Tomorrow
- **Day 9: the AST knowledge graph, and the console Day 7 and 8 both deferred.** Entities and edges from the Python AST — what depends on what — and the retrieval desk that finally makes all of this visible: a search box, the six configurations, and the score of each one next to it.
