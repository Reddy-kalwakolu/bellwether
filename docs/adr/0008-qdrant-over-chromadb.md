# ADR-0008: Qdrant over ChromaDB, for named vectors

**Date:** 2026-07-22
**Status:** Accepted — supersedes the vector-store choice in §4.1 of the design spec

## Context
The design spec named ChromaDB, on the reasoning that it is local, simple, and free — which was the right filter to apply before there was a requirement to compare embedding engines against each other.

ADR-0007 introduced that requirement. Four engines now embed the same corpus, and the comparison is only meaningful if every engine sees **identical chunks**. A comparison where each engine gets its own collection, its own import run, or its own chunking pass is not a comparison; it is four anecdotes that happen to share a chart.

## Decision
**Qdrant, with one point per chunk and one named vector per engine.**

A chunk of ADR-0005 is a single point in a single collection, carrying its Gemini vector (3072-dim), its Voyage vector (1024-dim), its potion vector (512-dim) and its hashing vector (256-dim) simultaneously, alongside one shared payload holding the provenance a citation needs. Switching engines is then a `using=` parameter on the query.

That is the whole argument. Fairness stops being a discipline someone has to maintain and becomes a property of the schema.

Reached over its REST API with `httpx`, which the project already depends on. A vector-database client library would be a second way to describe the same four calls.

An `InMemoryVectorStore` satisfies the same `VectorStore` protocol — the test default, and a legitimate fallback, since 585 chunks do not strictly need a server.

## Alternatives considered
- **ChromaDB**, as the spec said: rejected. No clean equivalent to named vectors, so each engine would need its own collection — reintroducing exactly the drift the comparison exists to eliminate. Widely described as the prototype tier with an expected migration to Qdrant or pgvector once filtering or scale grows; starting on the destination avoids a forced move.
- **pgvector on the existing Postgres:** genuinely tempting, since Postgres is already in the Compose stack and it would add no new service. Rejected because it puts the AI foundation's storage *inside the ads platform's database*, blurring the substrate/foundation boundary the whole project is built on. The substrate is the thing being operated on; it should not also be where the operator keeps its notes.
- **LanceDB:** rejected. Younger, weaker multi-process concurrency, smaller community — and its strength (embedded, serverless, multimodal) solves problems this project does not have.
- **Nothing — keep vectors in the JSONL corpus:** rejected, though closer to viable than it sounds at this size. It fails on payload filtering, which Level 1 needs immediately for `source_type` and `component` facets.

## Consequences
- Engine choice is a query parameter. The desk switches engines without re-importing anything.
- **`PUT /points` replaces a point outright.** Writing a second engine's vector that way silently wipes the first engine's, leaving a comparison that looks complete and is not. Verified against a running Qdrant, not assumed: new points are created with payload and vector together, and existing points are extended through `PUT /points/vectors`, which merges. This is the single most dangerous behaviour in this ADR and it is covered by a regression test.
- Payload is written when a point is created and not rewritten by later engines, because within one run every engine sees the same chunks. A chunk whose *text* changed keeps its `chunk_id`, so the honest way to pick that up is `--rebuild`, not a guess.
- Per-engine counts come from `has_vector` filters rather than the collection config — a *declared* named vector and a *populated* one are different things, and the desk would otherwise show a column of confident zeros as full.
- One more container on a 16 GB laptop. Qdrant is Rust and the collection is ~8 MB of vectors, so this is not the constraint it would be with a JVM-based store.

**The trigger that flips this:** if filtering and scale stay this modest through Level 3, revisit whether `InMemoryVectorStore` plus the JSONL corpus is enough and Qdrant is a container earning its keep on ceremony rather than need.
