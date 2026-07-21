# Day 5 — traffic-simulator + Level 0 quality gate

**Level:** 0 · **Date:** 2026-07-21

## Shipped
- `traffic-simulator`: seeded, reproducible ad-request load driven through the whole substrate — request an ad, and when it fills, report the impression (and sometimes a click) back to event-service
- **Five injectable failure modes**, switchable live over `POST /scenario`: `steady`, `error_burst`, `traffic_surge`, `bad_config_deploy`, `budget_runaway` — each self-describing at `GET /scenarios`
- Failures change **real configuration through public APIs** (ADR-0005). `bad_config_deploy` genuinely PATCHes every campaign's targeting; measured live, fill rate went 43% → 2%
- `steady` is also the **rollback**: it restores seed targeting, budgets, and caps. Recovery is a real operation, not an undo flag
- Three realistic seed campaigns with five creatives, created idempotently by name on first start
- Control plane: `/status`, `/scenarios`, `/scenario`, `/control` (pause/resume), `/seed`, `/metrics`, `/health`
- Metrics `sim_ad_requests_total{outcome}`, `sim_events_reported_total{event_type}`, `sim_scenario_info{scenario}`, plus a state-timeline panel on the ads-delivery dashboard so every anomaly lines up with the scenario that caused it
- Containerized on host port 8004; five Prometheus targets now up
- **`platform/level0_gate.py`** — the Level 0 quality gate. Eleven checks, exit 0 only if all pass, and it leaves the platform healthy
- **First published eval number: 11/11 · 100%**, on the running stack
- `docs/runbooks/level-0-substrate.md` — the first runbook, and deliberate Level 1 context-layer fodder
- 141 tests (16 new) passing with Docker stopped; ruff, ruff format, and mypy strict clean

## Decisions
- **ADR-0005: real failures, not mocked ones.** An ops agent evaluated against a mocked failure can only ever find the mock — the eval would measure the fixture, not the agent. So no service contains a branch that knows it is being tested: there is no `CHAOS=true`, no debug endpoint, no synthetic series written into Prometheus.
- **Real changes force real recovery.** Once `bad_config_deploy` actually rewrites targeting, "go back to normal" stops being free. `steady` restores the seed configuration — which is precisely the remediation a Level 3 guided-resolution agent should recommend.
- **The seed set is product, not fixture.** Rollback is defined as "restore the seed configuration", so those three campaigns have to be realistic, present, and safe to re-apply.
- **Rate is sized against the budgets.** At 5 rps the seed campaigns' pacing correctly throttled almost everything, which made a healthy baseline indistinguishable from an injected failure. 2 rps sits inside the pacing allowance and gives a ~40% baseline with room to collapse.
- **The gate's decision logic is pure, and the live run only supplies numbers.** That is what makes it trustworthy in both directions: it cannot pass with the substrate switched off, and it cannot fail because a test double drifted from reality.
- **"Not enough traffic" is not a pass.** `bad_config_deploy` needs at least 10 requests in the window before a low fill rate counts as collapse. A gate that passes on no evidence is worse than no gate.

## What running it found
The point of pointing real load at a real platform is that it finds real defects. Day one found three.

1. **Budgets overflow a 32-bit column.** `budget_micros` is a SQLAlchemy `Integer`, so Postgres caps it at 2,147,483,647 micros — about **$2,147 of daily budget**. `budget_runaway` tried to exceed it and Postgres raised `NumericValueOutOfRange`. The ceiling is now stated in the API contract as a `le=` bound, and the real fix — migrating the column to `BIGINT` — is exactly the kind of change ADR-0002 is waiting on Alembic for.
2. **That overflow surfaced as a bare 500**, in direct violation of the project's own coding standard. Now a typed 422, with a `DataError` handler so no column's range error can ever come back untyped.
3. **The seed guard was all-or-nothing.** One leftover campaign from a Day 4 manual test suppressed the entire seed set, and the simulator drove traffic at a single narrowly-targeted flight with a **2% fill rate** — 828 candidates rejected for `targeting_mismatch`. Seeding is now idempotent per campaign, by name.

And the gate failed itself on its first run: the `traffic_surge` check read its baseline from the switch's own response, which already reflected the new scenario, so it compared 20 rps against 20 rps. Caught, fixed, reran clean.

## For the video
1. `GET /scenarios` — read the five failure modes straight off the API. They are data, not branches
2. The ads-delivery dashboard under `steady`: ~40% fill rate, impressions and clicks flowing, spend accumulating
3. Inject `bad_config_deploy` live. Watch fill rate fall off a cliff and the `targeting_mismatch` band take over "why candidates lost"
4. **The payoff shot:** open campaign-service and show the *actual* PATCHed targeting. There is no mock — the decision path did its job perfectly, against configuration that was wrong. Say why that matters for Level 3
5. `steady` again, and watch it recover — recovery is a rollback, and it is the same operation an ops agent would recommend
6. `error_burst`: the error-ratio panel lifts, and the 422 body names the failing field. A caller error, correctly attributed to the caller
7. Run the Level 0 gate on screen. Eleven checks, `LEVEL 0 GATE: 11/11 (100%)`, exit 0
8. The eval scoreboard's **first published number** — and the honest story of the three defects the simulator found on its first day, including the $2,147 budget ceiling
9. Level 0 is complete: four services, real traffic, real metrics, real dashboards, real injectable incidents. Tomorrow the AI layer gets something real to operate on

## Tomorrow
- Day 6 opens **Level 1 — the Context Layer**: the document ingestion pipeline. Everything built across Days 1–5 becomes the corpus — code, five ADRs, five devlogs, the runbook written today, the OpenAPI specs of four services. The substrate stops being the thing we are building and becomes the thing the AI knows about.
