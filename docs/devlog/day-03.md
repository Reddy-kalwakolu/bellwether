# Day 3 — ad-decision-service

**Level:** 0 · **Date:** 2026-07-21

## Shipped
- `ad-decision-service`: the serving path — `POST /ad-request` takes a member context and a slot, returns a filled ad or an explained no-fill
- The filter chain in `decisioning.py`: eligibility → targeting → brand safety → frequency capping → budget pacing → creative fit, each rejection carrying a named reason
- Frequency capping and even budget pacing backed by Redis under day-scoped, self-expiring keys (`freq:` and `spend:`, 48h TTL)
- `CampaignClient` reads the active campaign set from campaign-service over HTTP — never its tables
- Every response carries a full candidate trace: which campaign lost, and to which rule
- Decision metrics `ad_decisions_total{outcome}` and `ad_candidates_filtered_total{reason}`, plus one JSON log line per decision with `member_id`, `slot_id`, `filled`, `no_fill_reason`, `latency_ms`
- Containerized on host port 8002, wired into Compose behind healthy Redis and campaign-service, registered as a Prometheus scrape target
- 28 new tests (51 total) passing with Docker stopped; ruff, ruff format, and mypy strict clean

## Decisions
- **ADR-0003: Redis holds the decision state, Postgres holds the truth.** Frequency counters and daily spend are per-member, written on every impression, and worthless after midnight — a counter with a TTL, not a row. The trigger that flips it: Day 4's event-service, when impressions become durable and spend becomes billable.
- **The decision path reads campaign-service's API, not its database.** That is what keeps ADR-0002's Alembic trigger — "the first time a second service reads these tables" — deliberately unfired.
- **Pure rules, injected state.** `decisioning.py` has no I/O: it takes a store and a clock. Every rule is unit-tested directly, and the whole suite runs with no Redis and no campaign-service.
- **Named reasons, not booleans.** A filter that returns `False` tells you nothing at 2am. `pacing_throttled` is a Prometheus label, a log field, and a line in the API response.
- **The slot goes to the campaign with the most daily budget left**, not the first one that matches. Otherwise whichever campaign sorts first drains its day by 9am.
- **Shared request observability.** Prometheus keeps one registry per process, so `http_requests_total` defined in two services collides the moment both are imported. The metrics and the request log line moved into `substrate/shared/observability.py`; the `service` label is what separates them.
- Test directories became packages — two services each owning a `test_api.py` collides in both pytest and mypy otherwise.

## For the video
1. Read `decisioning.py` top to bottom on screen — six rules in order, and name each one from the Netflix posting: targeting, brand safety, frequency capping, pacing
2. `POST /ad-request` → a fill. Then the same request twice more, then a fourth: `frequency_capped`, straight out of Redis. `docker exec` into Redis and show the `freq:` key with its TTL
3. Change the slot's content category to one the campaign excluded → `brand_safety_excluded`, with the losing campaign named in the trace
4. The trace field is the point: every response says which campaign lost and to which rule. Say why — Level 3's RCA agent will read exactly this
5. `docker compose logs ad-decision-service` — one JSON line per decision, with `no_fill_reason` as a first-class field
6. Prometheus: `ad_candidates_filtered_total` broken out by `reason`. That single graph is a fill-rate diagnosis
7. Kill campaign-service and fire an ad request → a typed 503, not a bare 500. Failure modes are a design surface, not an afterthought
8. ADR-0003 on screen — and the callback to ADR-0002: reading over HTTP is what kept the Alembic trigger from firing today

## Tomorrow
- Day 4: event-service — impression and click ingestion, aggregation — plus the observability stack: Grafana dashboards over the metrics both services are already emitting
