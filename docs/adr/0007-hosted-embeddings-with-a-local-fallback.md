# ADR-0007: Hosted embeddings by default, with a local engine for free iteration

**Date:** 2026-07-22
**Status:** Accepted — supersedes the embedding choice in §4.1 of the design spec

## Context
The design spec called for `sentence-transformers` running locally as the default embedding engine, on the reasoning that local inference is free and hosted APIs cost money. That reasoning was written before anyone measured the corpus, and it inverted once someone did.

The corpus is **585 chunks, about 149,000 tokens**. At current hosted rates that is **roughly two cents to embed in full**. Meanwhile `sentence-transformers` requires PyTorch: ~2.5 GB on disk, several minutes of install, and a resident process competing for RAM with Docker, Postgres, Redis, four substrate services, Prometheus and Grafana on a 16 GB laptop with no discrete GPU.

So the premise was backwards. The *best* option is also the *lightest* one locally, because the computation happens on the provider's hardware. The thing local inference was supposed to save — money — turns out to be a rounding error, and the thing it costs — install weight, RAM, and CI time — is the actual scarce resource.

There is a second constraint the spec did not account for. ADR-0006 committed to ingestion being reproducible with nothing running and no network. A test suite that downloads model weights from HuggingFace, or a CI job that installs 2.5 GB of PyTorch on every push, breaks that promise in exactly the way ADR-0006 rejected for OpenAPI specs.

## Decision
**Four engines behind one `Embedder` protocol, chosen per run rather than committed to once.**

| Engine | Dimensions | Role | Cost for the corpus |
|---|---|---|---|
| `gemini-embedding-001` | 3072 | Quality tier — #1 on the English MTEB board | **$0.0223, measured** |
| `voyage-3.5` | 1024 | Second hosted opinion; Anthropic's recommended family | ~$0.009 list |
| `potion-retrieval-32M` | 512 | Free local iteration — offline, no GPU, no key | $0.00 |
| `hashing` | 256 | The CI engine — no dependencies, no network, deterministic | $0.00 |

The local tier is **Model2Vec/potion, not sentence-transformers**. Model2Vec precomputes the token embeddings a sentence-transformer would produce, so inference is a lookup and a mean rather than a forward pass. That collapses the install from ~2.5 GB to eleven packages in under a second — **numpy and a tokenizer, no torch** — at roughly 82% of `all-MiniLM-L6-v2`'s retrieval score.

`hashing` is a hashed bag-of-words with sign hashing. It is a real lexical embedder, not a stub: chunks sharing vocabulary genuinely score higher. That is what lets CI assert retrieval behaviour with no model, no key, and no socket.

Cost is computed from **the token count the provider reports**, never an estimate. A published cost figure that was calculated rather than billed is a guess wearing a dollar sign.

## Alternatives considered
- **`sentence-transformers` as a default dependency:** rejected. 2.5 GB in CI on every push, plus network access mid-test to fetch weights. It contradicts ADR-0006 directly, and buys quality that a hosted call already beats for two cents.
- **`Qwen3-Embedding-8B`** (70.6 MTEB, the best open-source score): rejected. Roughly 5 GB quantised and slow on an integrated-graphics CPU. The best open model is precisely the one this hardware cannot run, which is why the hosted tier exists.
- **FastEmbed**, the obvious "lightweight, no torch" alternative: rejected. Its own issue tracker carries benchmarks where it is *slower* than the sentence-transformers it replaces (~800 vs ~1300 msg/s on `all-MiniLM-L6-v2`). Its real win is install size, and potion beats it on both size and speed.
- **One engine, chosen once:** rejected, and this is the load-bearing part. The engine is a *parameter*. Anything else makes "which embedding model should we use" an opinion rather than a measurement, and Level 1's whole claim is that these decisions get numbers.

## Consequences
- **CI installs no ML stack and touches no network.** `hashing` is the test default; the 288-test suite runs in about six seconds with Docker stopped and no keys set.
- **A missing API key disables an engine with an actionable reason, never a crash.** Every engine answers `available() -> (bool, str)`.
- Hosted engines are **rate-limited on free tiers below what one full pass needs** — Gemini allows 100 embed requests per *day* free, and the corpus needs 585. Both engines retry a 429 or 5xx with bounded backoff rather than abandoning a half-embedded corpus, but a full run requires billing enabled. That is a real operating cost of the quality tier and it belongs in the comparison, not in a footnote.
- `model2vec` pulls numpy, whose 2.5 stubs use PEP 695 syntax that mypy rejects against this project's 3.11 target — pinned to `numpy<2.5` rather than raising the target, because "Python 3.11+" is a promise the spec makes.

**The trigger that flips this:** corpus growth past a few million tokens, where per-run embedding cost stops being a rounding error and starts being a line item. At that point the local tier becomes the default and the hosted tier becomes the thing you run before a release.
