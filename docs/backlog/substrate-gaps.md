# Substrate gaps — the agent backlog

**Status:** Open · **Owner:** the Level 2 dev-lifecycle agents · **Created:** 2026-07-21

## What this is

Level 0 is a deliberately miniature ads platform. The gaps between it and a production
connected-TV ad system are known, named, and — for the most part — intentional. That
reasoning lives in `docs/site/level-0.html`, section 10.

This file is the other half of that decision: **the gaps worth closing, queued as work
for the dev-lifecycle agents rather than for a human.**

That framing is the point. "Watch an agent add a feature to a to-do app" demonstrates
nothing. "Watch an agent implement a second-price auction on the serving path — grounded
in the existing decision records, generating its own tests, surviving mutation testing,
passing a PR review that catches a seeded bug, and deploying behind a validation gate" is
a real task on a real system with a real quality bar. Every item below was chosen to be
exactly that.

## Selection principle

Ranked by **domain credibility gained per hour of work**, with a hard filter: an item
earns a place only if it is *also* a good agent task. That means it must be

- **bounded** — touches a small number of files with clear seams,
- **verifiable** — has acceptance criteria a test can assert,
- **grounded** — an existing ADR, runbook or standard constrains how it should be done,
- **non-cosmetic** — a reviewer who knows ad tech would notice it was missing.

## Constraints every item inherits

Non-negotiable, and the agents are graded on them:

- The whole test suite passes with Docker stopped. No item may introduce a test that
  reaches real infrastructure.
- `ruff`, `ruff format --check`, and `mypy --strict` clean across `tests`, `substrate`
  and `platform`. Reproduce the CI command verbatim — a superset run locally hides
  resolution failures.
- Every rejection carries a **named reason**, never a bare boolean. New reasons must
  appear in the API response, as a Prometheus label value, and in the JSON log line.
- Typed error envelopes only. Never a bare 500.
- Pure decision logic stays pure: `decisioning.py` performs no I/O.
- Ads-domain vocabulary only.
- Any decision that would contradict or extend an existing ADR requires a new ADR —
  including the condition that would reverse it.

---

## SG-01 · Second-price auction on the serving path

**Rank:** 1 · **Size:** M · **The headline task.**

Today the slot goes to whichever eligible campaign has the most daily budget remaining.
That is a delivery-balancing heuristic, and the deep dive names it as the single largest
simplification in the substrate. Real platforms run an auction: campaigns bid, the
winner is chosen on expected value, and the price paid is derived from the runner-up.

This is the gap a Netflix ads engineer notices first, and it sits directly on the
best-tested, purest code in the repo.

**Scope**
- Add `bid_micros` to the campaign model and its schemas.
- Introduce an auction step that runs over the eligible set *after* the filter chain.
- Second-price clearing: the winner pays the runner-up's bid plus one micro, floored at
  a reserve price and capped at its own bid.
- A single-bidder auction clears at the reserve price.
- Price the recorded impression at the cleared price, not a flat constant.

**Acceptance criteria**
- The winner is the highest bidder among eligible candidates, ties broken deterministically.
- Clearing price is strictly less than or equal to the winner's own bid, in every case.
- With one eligible candidate, the clearing price equals the reserve.
- The decision trace names the auction outcome per candidate (`won`, `outbid`), keeping
  the existing filter reasons untouched.
- `ad_decisions_total` gains no new cardinality; a new `ad_clearing_price_micros`
  histogram is added instead.
- Spend recorded in Redis and reported to event-service both use the cleared price.
- Existing pacing and frequency-cap behaviour is unchanged — proven by the current tests
  still passing unmodified.

**Grounded in:** ADR-0003 (spend is an estimate, not billing — this must stay true),
`docs/standards/coding-standards.md`, `substrate/ad_decision_service/decisioning.py`.

**Why it is a good agent task:** pure functions, no I/O, dense existing test coverage to
regress against, and a clear mathematical property (`price <= bid`) that mutation testing
can attack.

---

## SG-02 · Widen budget columns to BIGINT, behind a real migration

**Rank:** 2 · **Size:** M · **Closes a known live defect.**

Budgets are stored in a 32-bit integer column, capping a daily budget at 2,147,483,647
micros — about **$2,147**. Day 5's simulator hit this while injecting a budget runaway
and Postgres raised a range error. The ceiling is currently papered over with a `le=`
validation bound so the API returns a typed 422 instead of a 500.

That is a workaround, not a fix. The real fix is a wider column, which requires
migrations — and ADR-0002 deferred Alembic precisely until there was a schema change
worth doing properly. This is that change.

**Scope**
- Introduce Alembic, with the initial revision reflecting the current schema.
- Migrate `budget_micros` and `daily_budget_micros` to `BigInteger`.
- Raise or remove the `MAX_MICROS` schema bound accordingly.
- Update ADR-0002 with an "Update" section recording that its trigger fired, in the same
  style as ADR-0003's Day 4 update.

**Acceptance criteria**
- A campaign with a daily budget above 2,147,483,647 micros is created successfully.
- The migration runs cleanly against a database created by the previous schema.
- `create_all` is no longer the mechanism of record for schema changes.
- The existing typed-422 path still covers genuinely out-of-range values.
- The `DataError` handler remains — it is defence in depth for every other column.

**Grounded in:** ADR-0002 and its trigger, `docs/devlog/day-05.md`, ADR-0003's update
section as the format to follow.

**Why it is a good agent task:** the trigger condition was written down in advance, so
the agent can be evaluated on whether it correctly identifies that the trigger has fired
and updates the record — a genuine test of grounded reasoning, not just code generation.

---

## SG-03 · Latency budget and upstream timeout policy

**Rank:** 3 · **Size:** S

Ad decisioning is a hard-realtime path: a decision that arrives late is worth nothing,
because the player has already moved on. The substrate currently has no concept of a
deadline. `ad-decision-service` will wait on `campaign-service` for its configured
timeout and then fail the whole request.

**Scope**
- A configurable per-request decision deadline.
- On breach, return a no-fill with reason `deadline_exceeded` rather than an error —
  a late no-fill is the correct ad-serving behaviour.
- Serve from the last known good campaign set when the upstream is slow, with the
  staleness recorded on the decision.
- A metric for deadline breaches and a histogram of decision latency separate from
  total HTTP latency.

**Acceptance criteria**
- A slow upstream produces a no-fill with `deadline_exceeded`, not a 503.
- A completely unavailable upstream still produces a typed 503 (existing behaviour).
- The deadline is enforced in the pure decision layer via an injected clock, so it is
  testable with no sleeping.

**Grounded in:** the existing `CampaignServiceError` handling in
`substrate/ad_decision_service/main.py`, and the named-reason principle.

---

## SG-04 · Multiple frequency-cap windows

**Rank:** 4 · **Size:** S

Capping is per campaign per member per **day** and nothing else. Real capping is
layered: per hour, per day, per week, and often per advertiser as well as per campaign,
so a member is not saturated by one brand across several flights.

**Scope**
- Cap definitions become a list of `{window, limit}` rather than a single daily integer.
- Redis keys extend to carry the window; TTL derives from the window length.
- Advertiser-level caps in addition to campaign-level.

**Acceptance criteria**
- A member at an hourly cap but under the daily cap is `frequency_capped`.
- An advertiser cap suppresses a campaign that is itself under its own cap.
- Existing single-daily-cap configurations keep working unchanged.
- Key TTLs match the window they serve; no key outlives its window by more than one period.

**Grounded in:** ADR-0003 (day-scoped, self-expiring keys — the same discipline must hold
for every new window), `substrate/ad_decision_service/store.py`.

---

## SG-05 · Pacing modes and catch-up

**Rank:** 5 · **Size:** S

Only even pacing exists. Real platforms offer at least even and ASAP, and even pacing
usually includes catch-up: a campaign that under-delivered this morning is allowed to
run slightly hot this afternoon to land on its daily target.

**Scope**
- A `pacing_mode` on the campaign: `even` (default) or `asap`.
- ASAP ignores the intra-day allowance and is bounded only by the daily budget.
- Catch-up for even pacing, bounded so it can never exceed the daily budget.

**Acceptance criteria**
- An ASAP campaign is never `pacing_throttled` while daily budget remains.
- A campaign that under-delivered earlier in the day receives a larger allowance later,
  and never exceeds `daily_budget_micros` across the whole day.
- Existing even-pacing tests pass unmodified, including the first-impression-of-the-day
  floor.

**Grounded in:** `pacing_allowance_micros` in `decisioning.py` and its floor comment.

---

## SG-06 · Creative rotation and weighting

**Rank:** 6 · **Size:** S

The longest creative that fits the slot always wins. A campaign with three creatives
effectively has one. Real campaigns rotate creatives by weight to manage wear-out.

**Scope**
- A weight per creative; selection is a weighted choice among those that fit.
- Per-member creative history so rotation is stable rather than random per request.

**Acceptance criteria**
- Over many requests, creative selection converges on the configured weights.
- Selection remains deterministic given a seeded source, so tests stay hermetic.
- A creative too long for the slot is never selected, at any weight.

---

## SG-07 · Guaranteed versus biddable inventory

**Rank:** 7 · **Size:** M · **Stretch — only after SG-01.**

Real ad platforms sell inventory two ways: guaranteed deals booked in advance with
delivery commitments, and biddable inventory competing in the auction. Guaranteed
demand generally takes priority, with pacing driving it toward its committed volume.

**Scope**
- A campaign type: `guaranteed` or `biddable`.
- Guaranteed campaigns are considered first; biddable ones compete for what is left.
- An impression goal and delivery-progress tracking for guaranteed campaigns.

**Acceptance criteria**
- A guaranteed campaign behind pace beats a higher-bidding biddable campaign.
- A guaranteed campaign that has met its goal stops competing.
- The decision trace makes the inventory type visible.

---

## Permanently out of scope

Named here so their absence reads as a decision rather than an oversight. Each is real,
substantial, and would add **nothing** to what this project exists to demonstrate.

| Not building | Why not |
|---|---|
| **Ad delivery** — SSAI, manifest manipulation, transcoding, CDN, VAST/VMAP, player beacons | Pure media plumbing. Enormous surface, zero agentic-engineering value. |
| **Identity and consent** — identity resolution, consent frameworks, regional privacy regimes, clean rooms, audience segments | Legally and ethically serious, and impossible to do responsibly as a demo. A toy implementation would be worse than none. |
| **Measurement and verification** — viewability, invalid-traffic detection, third-party measurement, reconciliation, invoicing | Depends on external vendors and real money. Spend here is explicitly an estimate (ADR-0003) and must stay that way. |
| **Streaming data platform** — Kafka, stream processing, columnar warehouse, batch/real-time split | The right answer at real scale and the wrong answer here. It would multiply operational surface while the idempotency lesson — the part that actually matters — is already demonstrated (ADR-0004). |
| **Multi-region and horizontal scale** | ADR-0001 chose infrastructure proportionate to the problem. Nothing about scale is being claimed. |
| **Authentication and multi-tenancy** | Correct to omit for a local demo stack; disqualifying for anything real. Stated plainly rather than hidden. |
| **Forecasting and avails prediction** | A modelling project in its own right. Would consume the time the AI layer needs and demonstrate a different skill. |

## How this list gets used

1. **Days 11–15 (Level 2).** Items become tasks for the code-generation and
   test-generation agents. SG-01 is the flagship end-to-end demo: ticket → generated code
   → generated tests → mutation score → PR pre-review → deployment validation.
2. **Day 20 (Level 3).** Any defect an agent introduces and the ops agents then catch is
   the strongest possible evidence the loop works.
3. **Day 28.** The eval dashboard publishes how many items the agents closed unaided,
   and at what quality.
4. **After Day 30.** The remainder becomes the open-source roadmap — with the project's
   distinguishing claim being that the backlog is worked by its own agents, and the
   numbers are published.
