# ADR-0003: Redis holds the decision state, Postgres holds the truth

**Date:** 2026-07-21
**Status:** Accepted

## Context
ad-decision-service needs two pieces of state on the serving path: how many times a member has already seen a campaign today (frequency capping), and how much a campaign has spent today (budget pacing). Both are written on every served impression, read on every ad request, scoped to a single day, and worthless once that day rolls over. The access pattern is a counter with a TTL, not a row with a history.

Campaign configuration is the opposite: low-write, long-lived, relational, and already owned by campaign-service's Postgres.

## Decision
Per-day frequency counters and pacing spend live in Redis under self-expiring, day-scoped keys (`freq:{member}:{campaign}:{date}`, `spend:{campaign}:{date}`, 48h TTL). campaign-service's Postgres remains the system of record for campaigns, budgets, targeting, and creatives.

ad-decision-service reads that configuration over the campaign-service **HTTP API**, never its tables — which is why ADR-0002's Alembic trigger ("the first time a second service reads these tables") has deliberately not fired.

The store sits behind a `DecisionStore` protocol with two implementations: `RedisDecisionStore` in production, `InMemoryDecisionStore` in tests. The test suite therefore needs no infrastructure at all.

## Alternatives considered
- **Postgres tables for counters:** rejected. An impressions table would need a row per impression plus an aggregate query on every ad request, and a nightly cleanup job to delete data nobody wants. Redis expires it for free.
- **In-process counters:** rejected. They would not survive a restart and would be wrong the moment a second replica exists.
- **Reading campaign rows directly from Postgres:** rejected. It is the fastest way to make two services co-own one schema, and it would trip ADR-0002's trigger for no benefit — the API already returns exactly the active set the decision path wants.

## Consequences
- Decision state is lost if Redis is flushed. The worst case is a member briefly seeing one extra ad and a campaign briefly pacing as if the day just started. Acceptable for counters; not acceptable for money.
- Spend tracked here is an **estimate** at a flat per-impression price, not billing. Nothing downstream should treat it as revenue.
- Every rule that reads state does so through the protocol, so the hermetic test suite exercises the same code path production does.

**The trigger that flips this decision:** Day 4's event-service, which makes impressions durable. Once impressions are persisted and spend becomes billable, Postgres becomes the source of truth for spend and Redis keeps only the hot counters that back the serving-path decision.

## Update (Day 4) — the trigger fired, and the decision narrowed

Day 4 shipped event-service, so the trigger named above has now been pulled. What happened is worth recording, because it is not what "trigger fired" usually implies.

The decision did not reverse. It **narrowed**, exactly along the line it was drawn on:

- **Postgres now holds the record of what was served.** `ad_events` carries one durable, auditable row per impression and click, keyed on the caller's event id (ADR-0004). That is the artifact you reconcile against, reprocess, and hand to an RCA agent.
- **Redis keeps only the hot serving-path counters.** `freq:` and `spend:` are still read on every ad request and still expire themselves at the end of the day. They were never the record; they were the working set, and now there is a record behind them to be the working set *of*.

The two now disagree by design, and the disagreement is meaningful: Redis spend is what the pacer believed at decision time, `ad_events` spend is what was actually delivered. A gap between them is not a bug — it is reporting loss, and it is a panel on the ads-delivery dashboard.

Neither store changed. Naming the trigger in advance is what made it obvious, on the day, that the answer was "keep both, and be precise about which question each one answers."
