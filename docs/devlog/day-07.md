# Day 7 — Chunking, embeddings, and a fair comparison

**Level:** 1 · **Date:** 2026-07-22

## Shipped
- **Structure-aware chunking**, four strategies routed by document kind: Python split at symbol boundaries via the AST, Markdown at heading boundaries carrying the full heading path, OpenAPI one chunk per operation, and a fixed window as the fallback nothing is allowed to fail past
- Every chunk carries an **anchor** — the symbol, heading path, or route that names it. `substrate.traffic_simulator.driver.tick`, `ADR-0005 › Alternatives considered`, `POST /ad-request`. A chunk that cannot name itself can be returned but not defended
- **The chunking comparison, measured:** 585 structural chunks at **97% anchor coverage** against 349 naive-window chunks at **0%**, with p50 dropping from 1,960 to 854 characters
- **Four embedding engines behind one protocol** — Gemini (3072d), Voyage (1024d), potion (512d, local, free), hashing (256d, no dependencies). Every call records tokens, cost, and latency
- **Qdrant**, one point per chunk with **one named vector per engine**, so all four engines are compared on byte-identical chunks (ADR-0008). 585 points, three engines populated
- **The full run: Gemini embedded 585 chunks, 148,914 tokens, in 10.1 s for $0.0223.** potion did the same corpus in 2.1 s for nothing
- Rate-limit backoff on both hosted engines; a 429 or 5xx is waited out, a 401 is not
- ADR-0007 and ADR-0008, both superseding the design spec on measured grounds
- 288 tests (87 new) passing with Docker stopped and **no API keys set**; ruff, ruff format, mypy strict clean

## Decisions
- **ADR-0007: hosted embeddings by default, and the spec was backwards.** It called for local `sentence-transformers` to save money. The corpus is 149k tokens — about two cents to embed with the best model available. Meanwhile PyTorch is 2.5 GB and a resident process competing with Docker on a 16 GB laptop. The quality tier turned out to be the *lighter* option locally, because the compute happens on someone else's hardware. The premise inverted the moment anyone measured the corpus.
- **The local tier is Model2Vec, not sentence-transformers.** Static embeddings — precomputed token vectors, so inference is a lookup and a mean. Eleven packages in 903 ms with no torch, at ~82% of MiniLM's retrieval score.
- **ADR-0008: Qdrant for named vectors.** Four engines on one point means the comparison is fair *by schema* rather than by discipline. Chroma has no clean equivalent, and pgvector would have put the AI foundation's storage inside the ads platform's own database.
- **The engine is a parameter, not a commitment.** Everything — the protocol, the named vectors, tomorrow's console — exists so "which embedding model" is a measurement instead of an opinion.
- **Cost comes from the provider's reported token count, never an estimate.** A published cost that was calculated rather than billed is a guess wearing a dollar sign.

## What running it found
1. **Qdrant silently destroys vectors, and the fake would never have shown it.** `PUT /points` *replaces* a point outright, so writing Voyage's vector wiped Gemini's. Every test passed. The comparison would have rendered beautifully and been a lie. Found only by pointing the store at a running Qdrant and asking whether the first engine's vector was still there — it was not. New points are now created with payload and vector together; existing points are extended through the merge-only endpoint. There is a regression test that says why.
2. **PowerShell's `-Encoding utf8` writes a BOM.** The first line of the `.env` arrived as `﻿VOYAGE_API_KEY`, so Voyage reported "no API key" while the file plainly contained one — a missing-credential symptom with a text-encoding cause. Read as `utf-8-sig` now. Day 6's line-ending trap and this are the same trap wearing a different hat.
3. **I committed with mypy failing.** The verification chain ran `mypy ... | tail -2`, so a non-zero exit was swallowed and nine type errors went in green. That is day-04's own execution note number 6, broken by the person who wrote it. Fixed in a commit that says so.
4. **Adding retry made an existing test sleep for three minutes.** A test asserting Gemini's 429 handling suddenly inherited a 30-second backoff, six times over. The clock is injected now — anything that waits in production has to be told what time it is in a test.
5. **The free tiers cannot embed this corpus.** Gemini allows 100 embed requests per *day* free; the corpus needs 585. Voyage throttles below the needed token rate without a payment method. Both engines back off and retry properly, and both still failed — because this is a billing ceiling, not a bug. The quality tier has an operating cost, and it belongs in the comparison rather than in a footnote.

## The number that makes the point
Asked *"how often can the same viewer be shown one advert?"* — a question containing none of the words *frequency*, *cap*, *member*, or *impression*:

| Engine | Top hit |
|---|---|
| gemini | `Substrate gaps › SG-04 › Multiple frequency-cap windows` |
| potion | `substrate.ad_decision_service.main.decide` |
| hashing | a Day 7 plan section — noise |

Gemini understood the question. potion found the neighbourhood. hashing matched nothing meaningful, exactly as designed. That gap is what the two cents buys, and it is the reason the comparison exists rather than a vendor chart.

## For the video
1. Open one chunked ADR and read the anchors aloud — `ADR-0005 › Alternatives considered`. **That is what a citation will be made of**
2. The chunking table: 97% anchor coverage against 0%. Naive windowing is not a straw man, it is the default everyone ships
3. `docker compose up -d qdrant`, then the run: three engines, 585 chunks each, one collection
4. The cost line. **$0.0223 for the best embedding model available**, on screen, from the provider's own token count
5. The frequency-capping query across all three engines, side by side. This is the shot
6. Open Qdrant's dashboard and show one point carrying three named vectors of three different sizes. Say why that makes the comparison fair
7. The honest part: the vector-wipe bug that every test passed through, and how a live server caught what a fake could not

## Tomorrow
- **Day 8: hybrid retrieval, reranking, and the embedding desk.** Vector search alone loses to keyword search on exact identifiers — `budget_micros` should not need semantics. BM25 joins the vector side, results are reranked, and the console lands: four channel strips, one armed, a search box, and the quality difference visible as you switch. Voyage still needs a payment method before it can complete a full pass.
