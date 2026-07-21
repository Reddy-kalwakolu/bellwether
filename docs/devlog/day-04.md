# Day 4 — event-service + Grafana dashboards

**Level:** 0 · **Date:** 2026-07-21

## Shipped
- `event-service`: `POST /events` ingests one impression or click and answers `201 recorded` or `200 duplicate` — never a double count
- Idempotency is the primary key. The caller supplies `event_id`, `ad_events.event_id` is the primary key, and the insert either wins or raises `IntegrityError`. No read-then-write check, so no race
- `ad_events` is append-only: one durable row per delivered impression and click, tied back to the `request_id` of the decision that produced it
- `aggregation.py` computes delivery on read — one `GROUP BY campaign_id` giving impressions, clicks, CTR, and impression spend, optionally scoped to a UTC day
- `GET /campaigns/{id}/delivery`, `GET /delivery`, and `GET /events` (newest first, filterable by campaign or member) — the raw tape behind the rollups
- Metrics `ad_events_total{event_type}`, `ad_events_duplicate_total`, `ad_spend_micros_total`, plus one JSON log line per ingest carrying `event_id`, `event_type`, `campaign_id`, `member_id`, `price_micros`, `status`
- Containerized on host port 8003, behind a healthy Postgres, registered as a Prometheus scrape target — four targets now up
- **Two Grafana dashboards provisioned from files in the repo**: `substrate-health` (targets up, request rate, error ratio, p95 — all by service) and `ads-delivery` (fill rate, why candidates lost, events ingested, CTR, spend rate, duplicates rejected)
- 12 new tests (87 total) passing with Docker stopped; ruff, ruff format, and mypy strict clean

## Decisions
- **ADR-0004: idempotent on the primary key, aggregated on read.** A delivery report is retried whenever a network hiccups — and Day 5's simulator will retry on purpose. A `SELECT`-then-`INSERT` races; two concurrent reports of the same impression both read "absent" and both insert. The primary key does not race. That is its whole job.
- **Server-generated event ids were never on the table.** They make idempotency impossible by construction: every retry is a new id and therefore a new impression.
- **A duplicate is a `200`, not a `409`.** From the caller's point of view the event *is* recorded. Returning an error would train clients to retry harder at exactly the wrong moment.
- **No rollup table.** A rollup is a second copy of the truth, and the two drift the first time anyone backfills. `/delivery` scans the table — fast because the table is small, not because the query is clever. The flip trigger is written down: `/delivery` p95 past ~200 ms under Day 5's load.
- **ADR-0003's trigger fired, and the decision narrowed instead of reversing.** Postgres now holds the auditable record of what was served; Redis keeps only the hot serving-path counters. They now disagree by design, and the disagreement is the interesting number: Redis spend is what the pacer *believed* at decision time, `ad_events` spend is what was actually delivered.
- **Dashboards are code, and a test guards them.** `tests/infra/test_grafana_dashboards.py` asserts every panel has a query, points at the pinned datasource uid, and graphs a metric some service actually emits. It runs with Docker stopped.
- **Pin the datasource uid.** Grafana invents one at first start otherwise, and every provisioned panel loads empty against a uid that no longer exists. The plan missed this; the live stack found it in thirty seconds.
- **Series color follows the entity, not the rank.** `campaign-service` is the same blue on every panel of every dashboard, fixed by name override, so a filter that drops a service never repaints the survivors. The palette was validated for colorblind separation against Grafana's dark canvas rather than eyeballed.

## For the video
1. `POST /events` with an explicit `event_id` → `201 recorded`. Post the **exact same body** twice more → `200 duplicate`, both times. Then `GET /delivery`: one impression. That is the whole idea in three curls
2. Show why the obvious alternative is wrong — sketch `SELECT` then `INSERT`, two clients, both reading "absent". Then show the primary key doing it correctly with no coordination at all
3. Add a click, then `GET /delivery` again: CTR appears. Point out that clicks carry no spend — only an impression costs the advertiser anything, and it is one line in the aggregation
4. `docker compose logs event-service` — one JSON line per ingest, with `status: duplicate` as a first-class field, not an exception buried in a stack trace
5. Grafana, live, both dashboards. Lead with **"Why candidates lost"** — stacked bands, one per rule, straight out of yesterday's `ad_candidates_filtered_total`. A fill-rate drop is read off the band that grew. That single panel is the payoff for naming every rejection on Day 3
6. Show that the dashboards are files: `infra/grafana/provisioning/dashboards/*.json`, in the repo, and `pytest tests/infra` failing the build if a panel loses its query. No click-ops, no exported JSON pasted by hand
7. ADR-0004 on screen, then scroll up to ADR-0003 and read the Day 4 update. The trigger was named a day in advance, it fired on schedule, and the answer was "narrow it" — that is what writing triggers down buys you
8. Close on `localhost:9090/targets`: four green. Tomorrow something starts pushing traffic through all of it

## Tomorrow
- Day 5: traffic-simulator with injectable failure modes — latency spikes, error bursts, bad-config deploys, budget runaway — driving real load through the decision path and into event-service, with the dashboards built today as the place the failures become visible. Then the Level 0 quality gate.
