# ADR-0009: Reciprocal Rank Fusion over weighted scores, and hybrid retrieval kept but not defaulted

**Date:** 2026-07-23
**Status:** Accepted

## Context
Day 7 gave the context layer semantic search. It could find chunks that *mean* what was asked and could not reliably find chunks that are *named* what was asked: embedding `budget_micros` produces a vector near "budget", "spending" and "cost", and no nearer the one chunk that defines the field than to twenty that discuss money generally.

The obvious fix is a lexical index beside the vector one, and the obvious question is how to combine two rankings whose scores are not on the same scale. Cosine similarity lives in `[-1, 1]`. BM25 is unbounded and routinely reaches 20 on this corpus.

There is a second, sharper problem. Day 8's entire deliverable is an honest comparison of retrieval configurations. Any combination scheme that requires normalising two score distributions introduces a tuning knob — and a knob can be turned, consciously or not, until the configuration you were hoping to promote comes out ahead.

## Decision
**Reciprocal Rank Fusion at `k = 60` is the fusion default, and it reads rank position only — never the underlying scores.**

```
score(chunk) = Σ  1 / (k + rank_in_list_i)
```

`k = 60` is the published constant from Cormack et al. (2009). It is deliberately **not** tuned against the gold set, because a fusion constant fitted on the evaluation set is the evaluation set's constant.

A `weighted_fusion` alternative — min-max normalise each side, then `α·dense + (1-α)·lexical` — ships **alongside** it rather than as a rejected idea, and appears in the published comparison as its own row. If it wins, it wins.

**Hybrid retrieval is built, measured, and is not the default.** On this corpus, dense retrieval alone scored nDCG@10 **0.670** against RRF hybrid's **0.628**; adding the lexical side lowered the score. The hybrid path stays available behind a mode flag.

## Alternatives considered
- **Score normalisation as the default combiner:** rejected as the default for the reason above — every normalisation scheme is a tuning knob, and this day's product is a number that must not be tunable. Kept as a measured row, where it earned its place: on *conceptual* queries weighted fusion was the best fusion (0.620 against RRF's 0.583).
- **Learned fusion weights:** rejected. There are 26 gold queries; there is nothing to train on. Fitting weights on the evaluation set is the same circularity the pooled-judgement design exists to prevent.
- **Lexical-only retrieval:** rejected on measurement. BM25 alone scored 0.395 overall and **0.426 on the identifier category it was expected to dominate** — behind dense's 0.711. Retained as a comparison row and as the fast path (2.9 ms against dense's 812 ms).
- **Dropping BM25 entirely now that dense wins:** rejected, but this is the closest call in the ADR. See the trigger below.

## Consequences
- Fusion has one constant and no dial. Two people running the comparison get the same table.
- The identifier-aware tokenizer is load-bearing for the lexical side and is kept regardless: it emits every token both whole and split, so `budget_micros` contributes `budget_micros`, `budget` and `micros`, and an exact identifier match legitimately outscores a partial one.
- **The comparison is only valid because all six configurations share one code path.** `SearchService` derives candidates identically for every mode — same depth, same query embedding, same filters — so rows differ in the stage their name describes and in nothing else. A mode that quietly used a different candidate depth would make the table a comparison of accidents.
- `SearchService` refuses a query whose `config.engine` differs from the embedder that would encode it. Without that guard a caller iterating engines — which the eval harness does — could score one engine's vectors against another engine's query vector and get plausible, wrong numbers.
- Carrying a lexical index the default path does not use is real, ongoing cost: it is built on every search-service construction, and it is a component that can rot silently while nothing reads it.

**The trigger that flips this:** hybrid becomes the default the moment it beats dense on the gold set — most plausibly when the corpus grows past this one's character. 708 chunks of well-structured ADRs, devlogs and typed Python, embedded by a frontier model, is close to the best case for pure semantic search. A larger, messier, more jargon-dense corpus, or one with many near-duplicate documents distinguished only by identifiers, is where BM25 earns its keep. Re-run the comparison at Level 3 and at any significant corpus growth. Conversely, if BM25 is still unused two levels from now, delete it rather than maintaining a component nothing reads.
