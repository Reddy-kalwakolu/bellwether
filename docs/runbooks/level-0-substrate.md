# Runbook — Level 0 substrate

The mini ads platform: what runs, how to drive it, how to break it on purpose, and
how to read what happens. Written for a human on call, and deliberately structured
for the Level 1 context layer to ingest.

## What runs

| Service | Host port | Responsibility | State |
|---|---|---|---|
| campaign-service | 8001 | System of record: campaigns, budgets, targeting, brand safety, creatives | Postgres |
| ad-decision-service | 8002 | The serving path: targeting → brand safety → frequency capping → pacing → selection | Redis (counters) |
| event-service | 8003 | Impression and click ingestion, delivery aggregation | Postgres |
| traffic-simulator | 8004 | Seeded load, and five injectable failure modes | — |
| Prometheus | 9090 | Scrapes every service's `/metrics` every 5s | — |
| Grafana | 3000 | Two provisioned dashboards in the **BELLWETHER** folder | — |

Postgres is on 5433 and Redis on 6380 — deliberately off the default ports so the
stack cannot collide with other local database instances.

## Starting and stopping

```bash
docker compose up -d --build
docker compose restart prometheus grafana   # neither hot-reloads provisioning
docker compose ps                           # every service should read healthy
docker compose down                         # stop; add -v to also drop the database
```

The simulator seeds three campaigns on first start and begins driving traffic
immediately. Seeding is idempotent by campaign name, so restarting is always safe.

## Reading the dashboards

**Substrate — service health.** Scrape targets, request rate, error ratio, and p95
latency, all cut by service. Every panel colours a service the same way everywhere.

**Ads — decisioning and delivery.** Fill rate, decisions by outcome, *why candidates
lost*, events ingested, CTR, spend rate, duplicate reports, and a state-timeline of
the currently injected scenario.

**"Why candidates lost" is the diagnostic panel.** It is stacked, one band per rule.
A fill-rate drop is read off whichever band grew. The bottom timeline says whether a
failure mode was deliberately injected — check it before investigating anything.

## Injecting a failure

```bash
curl -s localhost:8004/scenarios                     # the catalogue, self-describing
curl -s -X POST localhost:8004/scenario \
  -H 'content-type: application/json' -d '{"name":"bad_config_deploy"}'
curl -s localhost:8004/status                        # ticks, fills, no-fills, rejections
```

| Scenario | What it really does | Signal | Recovery |
|---|---|---|---|
| `steady` | Restores seed targeting, budgets, and caps | Fill rate ~40% | — (this *is* the recovery) |
| `error_burst` | Sends malformed ad requests (30%) | Error-ratio panel lifts; 422s | Switch to `steady` |
| `traffic_surge` | Raises request rate 10× | Request rate and p95 climb together | Switch to `steady` |
| `bad_config_deploy` | `PATCH`es every campaign's targeting to `AQ` | Fill rate → ~2%; `targeting_mismatch` band takes over | Switch to `steady` |
| `budget_runaway` | Inflates one campaign's daily budget and cap | `pacing_throttled` vanishes; one campaign takes every slot | Switch to `steady` |

Failures change **real configuration** (ADR-0005). `steady` is a genuine rollback,
not an undo flag — which is why it is also the remediation an ops agent should
recommend.

## Symptom table

| Symptom | Most likely cause | What to check |
|---|---|---|
| Fill rate at or near zero | Targeting no longer matches traffic | "Why candidates lost" — if `targeting_mismatch` dominates, check the injected-scenario timeline, then `GET /campaigns` targeting |
| Fill rate falling gradually through the day | Pacing working correctly | `pacing_throttled` band growing. Budgets are being spread across the day on purpose |
| `frequency_capped` band growing | A small member pool seeing the same campaign | Expected under sustained load with a bounded population |
| Error ratio spiking, 422s | A caller sending invalid requests | The 422 body names the failing field. Check whether `error_burst` is injected |
| Error ratio spiking, 5xx | A real fault | `docker compose logs <service>` — every service logs one JSON line per request |
| p95 climbing *with* request rate | Load, not a fault | Compare the request-rate and p95 panels; if they move together it is throughput |
| Duplicate reports climbing | A client retrying delivery reports | Working as designed — the primary key is deduplicating them (ADR-0004) |
| `no_fill_reason: no_candidates` | No active campaigns at all | `GET /campaigns?status=active`; `POST localhost:8004/seed` re-creates the seed set |
| ad-decision-service returning 503 | campaign-service unreachable | `docker compose ps`; the 503 is typed and names the upstream |
| A campaign 422s on budget | Budget above ~2.1 billion micros | Budgets live in a 32-bit column. That ceiling is real (see day-05 devlog) |

## Running the quality gate

```bash
python -m uv run python platform/level0_gate.py
```

Eleven checks: five health endpoints, one Prometheus scrape check, and one per
failure mode. Exit 0 only if all eleven pass. It leaves the platform healthy.

## Useful queries

```bash
curl -s localhost:8003/delivery                       # impressions, clicks, CTR, spend
curl -s 'localhost:8003/events?limit=5'               # the raw delivery tape
curl -s 'localhost:8001/campaigns?status=active'      # what the decision path can see
docker compose logs ad-decision-service --tail 20     # one JSON line per decision
```
