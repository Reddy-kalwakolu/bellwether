# ADR-0010: Reranking behind a protocol — the free one is the default, the LLM one is not

**Date:** 2026-07-23
**Status:** Accepted

## Context
Fusion produces a candidate list; reranking decides what sits at the top of it. "Reranking" in the literature usually means a cross-encoder or an LLM scoring each candidate against the query, and a day that claims to have shipped reranking should mean something by the word.

Two constraints shaped the choice. ADR-0006 and ADR-0007 rule out heavyweight local ML — no `sentence-transformers`, no PyTorch in CI, no network mid-test. And the test suite must stay hermetic: the full run passes with Docker stopped and no API keys set.

## Decision
**A `Reranker` protocol with two implementations, shaped deliberately like Day 7's `Embedder`** — `spec`, `available() -> (bool, reason)`, one verb. The recurring lesson of this project is that the engine is a parameter, not a commitment, and a reranker that cannot run reports why in words a human can act on rather than crashing.

**`HeuristicReranker` is the default.** Free, deterministic, offline, and a real reranker rather than a stub: exact-identifier match, anchor-path match, a source-type prior (an ADR outranks a devlog for a "why" question), and a length penalty for chunks so long the match is incidental. Because it is real, the hermetic suite can assert reranking *behaviour* with no model and no network — which is what keeps the whole suite hermetic.

**`LLMReranker` is available behind a flag, and is not the default.** It grades each candidate 0/1/2 through the LLM seam using structured output, so a malformed ranking is impossible rather than merely unlikely. Its base score derives from **rank position, never the incoming score** — the candidate list arrives from modes whose scores differ by three orders of magnitude (RRF ≈ 0.016, BM25 ≈ 20), and a boost calibrated against one would be swamped or invisible against the other. That is ADR-0009's scale-mixing problem in a second place.

The measurement decided the default:

| Reranker | nDCG@10 | MRR | p50 latency | Cost |
|---|---|---|---|---|
| none (RRF hybrid) | 0.628 | 0.844 | 842 ms | — |
| heuristic | 0.639 | 0.791 | 783 ms | free |
| LLM (Gemini) | **0.656** | **0.870** | **9,309 ms** | ~$0.0002/query |

The LLM reranker is the best hybrid row and holds the best MRR of any configuration — the first genuinely useful answer is nearly always rank 1. It also costs **12× the latency** of the free heuristic and **still loses to plain dense retrieval at 0.670**. That is not a margin worth nine seconds on the serving path.

## Alternatives considered
- **A local cross-encoder** (`sentence-transformers` re-rankers): rejected. 2.5 GB of ML stack in CI on every push and network access mid-test, contradicting ADR-0006 and ADR-0007 directly.
- **A dedicated hosted rerank API** (Cohere `rerank`, Voyage `rerank`): the purpose-built tool, and the strongest quality story. Deferred, not rejected — Voyage has no payment method on this account (Day 7 execution note 5), and a second billing relationship was not worth opening on the day the eval harness itself was being built. This is the first thing to try next; see the trigger.
- **Heuristic only, no LLM path:** rejected. It would have left the day's central claim weaker than the industry meaning of "reranking", and — more importantly — the LLM path is what exposed the two integration bugs below. Building it paid for itself in findings even though it is not the default.
- **Making the LLM reranker the default because it is the best hybrid row:** rejected on latency and on the fact that it loses to a configuration with no reranking at all.

## Consequences
- A missing API key disables a reranker with an actionable reason; it never crashes.
- **A backend failure degrades to the fused order, not to an empty result.** An outage should cost a slightly worse ranking, not no answer — an empty list would score zero in the eval and measure the outage rather than the reranker.
- That degrade path is correct and it is an observability hole. **A completely broken reranker produced entirely plausible numbers.** The only thing that caught it was `hybrid-llm` scoring byte-identical to `hybrid` across every category — two rows that suspiciously agreed to three decimals. Degrade paths need to be loud somewhere, even when they are quiet in the response.
- **Two integration bugs were invisible to the hermetic suite and required one live call.** Gemini's `responseSchema` is a restricted OpenAPI dialect that rejects integer `enum` values (400, `TYPE_STRING`), so the 0/1/2 grading schema failed every request; and the default model `gemini-2.5-flash` now returns **404 — "no longer available to new users."** A fake transport can validate neither a provider's schema dialect nor its model catalogue. Model ids now use the `gemini-flash-latest` alias, and the grade scale is enforced by the parser rather than the schema.
- The LLM seam this reranker sits on is spec §4.6's first piece, and it outlives this decision — Level 2's agents use the same protocol.
- **The Claude backend is written and hermetically tested but has never run against the live API.** What is owed is `ANTHROPIC_API_KEY`, not code. Given the two bugs a live call exposed on the Gemini path, the Claude path should be assumed broken until one real request proves otherwise.

**The trigger that flips this:** try a dedicated hosted reranker (Cohere or Voyage) against this gold set. If it beats dense retrieval's 0.670 at a latency the serving path can afford, reranking becomes the default and this ADR is superseded. If the LLM reranker's latency ever drops near the heuristic's while keeping its MRR advantage, revisit likewise. If neither happens by the end of Level 3, delete the LLM path and keep the heuristic.
