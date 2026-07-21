# Day 1 — Foundation

**Level:** 0 · **Date:** 2026-07-20

## Shipped
- Repo scaffolding: uv, ruff (strict), mypy (strict), pytest, pre-commit
- README with architecture diagram and 30-day roadmap
- ADR-0001: Docker Compose over Kubernetes
- Infra skeleton: Postgres, Redis, Prometheus, Grafana — one command up, all healthy
- Running doc v1: docs/site/index.html (system diagrams, 30-day tracker, eval scoreboard)
- CI: lint + format + types + tests on every push/PR

## Decisions
- ADR-0001: Compose over k8s — infrastructure proportionate to the problem is the Staff-level signal
- Host ports 5433 (Postgres) / 6380 (Redis) to avoid collisions with other local stacks; in-network ports stay standard
- uv as package manager: fast installs on camera, single lockfile, modern default

## For the video
1. The empty folder → `git log` showing the day's commits (the arc of Day 1)
2. README architecture diagram rendered on GitHub — explain Substrate vs Bellwether in 60 seconds
3. `docker compose up -d` → `docker compose ps` all healthy (one command, four services)
4. Grafana at localhost:3000 already wired to Prometheus (provisioned, not clicked together)
5. The running doc in the browser: day tracker, diagrams, eval scoreboard with empty Actuals — "these numbers are the whole point of this series"

## Tomorrow
- Day 2: campaign-service (CRUD, Postgres, tests, OpenAPI)
