# BELLWETHER Level 0 / Day 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Day 1 of BELLWETHER: repo scaffolding with quality tooling, README, ADR-0001, Docker Compose infrastructure skeleton (Postgres, Redis, Prometheus, Grafana), CI workflow, and the HTML running doc v1 with system diagrams.

**Architecture:** Python monorepo managed with `uv` (single root `pyproject.toml`; service packages added on later days). Infrastructure via Docker Compose. Documentation site is static HTML + Mermaid (CDN) in `docs/site/`, publishable via GitHub Pages.

**Tech Stack:** Python 3.11+, uv, pytest, ruff (lint+format), mypy, Docker Compose, Postgres 16, Redis 7, Prometheus, Grafana, Mermaid.js.

## Global Constraints

- Python 3.11+; type hints on all functions; docstrings on public functions/classes
- Lint/format: ruff; types: mypy; tests: pytest (coverage target >80% once services exist)
- Conventional commits (`feat:`, `docs:`, `chore:`, `test:`, `ci:`)
- Use ads-domain language from the spec (ad insertion, brand safety, frequency capping, pacing)
- Every user-facing doc carries: "BELLWETHER is an independent open-source project, not affiliated with or endorsed by Netflix."
- Working directory: `C:\Users\Kcpre\OneDrive\Desktop\Netflix-build_in_public` (repo will be published to GitHub as `bellwether`)
- End of day: update `docs/site/index.html` day tracker + write `docs/devlog/` entry (definition of done)

---

### Task 1: Repo scaffolding, tooling, README

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.pre-commit-config.yaml`, `README.md`, `LICENSE` (Apache-2.0), `tests/test_sanity.py`

**Interfaces:**
- Produces: root `pyproject.toml` that later service packages extend (`[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]` sections are the repo-wide config)

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "bellwether"
version = "0.1.0"
description = "AI-native engineering platform for ad-tech teams: context layer, dev & ops agents, orchestration, evals - operating on a mini Netflix-style ads substrate."
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
dependencies = []

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.6",
    "mypy>=1.11",
    "pre-commit>=3.8",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ANN"]

[tool.mypy]
python_version = "3.11"
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
# NOTE: add "substrate" and "bellwether" to testpaths when those packages appear (Day 2+)
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.py[cod]
.venv/
.uv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
.env
*.egg-info/
dist/
data/
```

- [ ] **Step 3: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: check-yaml
```

- [ ] **Step 4: Create `tests/test_sanity.py`**

```python
"""Sanity check that the toolchain runs."""


def test_toolchain_alive() -> None:
    assert 1 + 1 == 2
```

- [ ] **Step 5: Create `README.md`**

Write the README with exactly these sections (prose to be written by executor, ~120 lines max):
1. `# BELLWETHER` + one-line tagline: "An AI-native engineering platform for ad-tech teams — built in public."
2. Badges placeholder row (CI badge added after first push)
3. **What is this?** — 3 short paragraphs: (a) the two-part system (Substrate = mini Netflix-style ads platform; Bellwether = AI foundation: context layer, dev agents, ops agents, orchestrator, evals), (b) the thesis: AI velocity with provable quality — every AI capability ships with numeric evals, (c) built in public over 30 days, link to video series (placeholder link).
4. **Architecture** — embed the system-overview Mermaid diagram copied verbatim from `docs/superpowers/specs/2026-07-20-bellwether-design.md` section 2.
5. **The 30-day roadmap** — compact 6-row table (level, days, theme) copied from spec section 5.
6. **Status** — "Day 1: Foundation" with checklist of Level 0 days.
7. **Quickstart** — `uv sync --group dev`, `uv run pytest`, `docker compose up -d`.
8. **Docs** — links to `docs/superpowers/specs/`, `docs/adr/`, `docs/site/index.html` (running doc), `docs/devlog/`.
9. **Disclaimer** — the not-affiliated line from Global Constraints.

- [ ] **Step 6: Create `LICENSE`**

Run: download Apache-2.0 text:
```bash
curl -s https://www.apache.org/licenses/LICENSE-2.0.txt -o LICENSE
```
(If offline, write the standard Apache-2.0 license text manually.)

- [ ] **Step 7: Install and verify toolchain**

Run: `uv sync --group dev`
Expected: resolves and installs pytest, ruff, mypy, pre-commit.

Run: `uv run pytest`
Expected: `1 passed`

Run: `uv run ruff check .` then `uv run ruff format --check .`
Expected: no errors (fix `tests/test_sanity.py` formatting if flagged).

Run: `uv run mypy tests`
Expected: `Success: no issues found`

(If `uv` is not installed: `pip install uv` first.)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: scaffold repo with uv, ruff, mypy, pytest, pre-commit and README"
```

---

### Task 2: Docs structure + ADR-0001

**Files:**
- Create: `docs/adr/0000-adr-template.md`, `docs/adr/0001-docker-compose-over-kubernetes.md`, `docs/devlog/.gitkeep`, `docs/runbooks/.gitkeep`, `docs/standards/coding-standards.md`

**Interfaces:**
- Produces: ADR format all future ADRs follow; `docs/standards/coding-standards.md` later grounds the PR pre-review agent (Level 2)

- [ ] **Step 1: Create `docs/adr/0000-adr-template.md`**

```markdown
# ADR-NNNN: Title

**Date:** YYYY-MM-DD
**Status:** Proposed | Accepted | Superseded by ADR-NNNN

## Context
What situation forces a decision?

## Decision
What we chose.

## Alternatives considered
What else was on the table, and why it lost.

## Consequences
What becomes easier, what becomes harder.
```

- [ ] **Step 2: Create `docs/adr/0001-docker-compose-over-kubernetes.md`**

Using the template. Content requirements:
- **Context:** BELLWETHER substrate is 4 small services + observability, run by one developer on one machine, demoed in videos; Netflix-style architecture signals matter, but so does Staff-level pragmatism.
- **Decision:** Docker Compose for all local orchestration. Accepted.
- **Alternatives:** Kubernetes (kind/minikube) — rejected: operational overhead, slower demos, zero additional proof of AI-engineering skill (the job is AI foundation, not cluster ops). Bare processes — rejected: no service isolation, no one-command setup, breaks the `<10 min` clone-to-running quality gate.
- **Consequences:** one-command startup, trivially reproducible in videos; if the project ever needs multi-node scale, Compose files translate cleanly to k8s manifests.

- [ ] **Step 3: Create `docs/standards/coding-standards.md`**

Content requirements (keep to ~40 lines; this file is consumed by the Level 2 PR-review agent, so make rules checkable):
- Type hints on all function signatures; mypy strict must pass
- Ruff clean; line length 100
- Every service endpoint has: request/response Pydantic models, structured log line with `service`, `endpoint`, `latency_ms`, and error handling that returns typed error responses (never bare 500s)
- Tests: every endpoint has at least one happy-path and one failure-path test; no test may depend on another test's state
- Naming: ads-domain terms (`campaign`, `creative`, `frequency_cap`, `pacing`, `brand_safety`) — never generic `item`/`thing`
- Commits: conventional commits, imperative mood

- [ ] **Step 4: Create empty `docs/devlog/.gitkeep` and `docs/runbooks/.gitkeep`**

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "docs: ADR template, ADR-0001 (Compose over k8s), coding standards"
```

---

### Task 3: Docker Compose infrastructure skeleton

**Files:**
- Create: `docker-compose.yml`, `infra/prometheus/prometheus.yml`, `infra/grafana/provisioning/datasources/prometheus.yml`

**Interfaces:**
- Produces: running Postgres at `localhost:5433` (user/pass/db: `bellwether`/`bellwether`/`bellwether`; in-network `postgres:5432`), Redis at `localhost:6380` (in-network `redis:6379`), Prometheus at `localhost:9090`, Grafana at `localhost:3000` (anonymous admin). Day 2+ services attach to the `bellwether` network and get scraped by adding jobs to `infra/prometheus/prometheus.yml`.

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
name: bellwether

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: bellwether
      POSTGRES_PASSWORD: bellwether
      POSTGRES_DB: bellwether
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U bellwether"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  prometheus:
    image: prom/prometheus:v2.53.0
    ports: ["9090:9090"]
    volumes:
      - ./infra/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro

  grafana:
    image: grafana/grafana:11.1.0
    ports: ["3000:3000"]
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: Admin
    volumes:
      - ./infra/grafana/provisioning:/etc/grafana/provisioning:ro

volumes:
  pgdata:

networks:
  default:
    name: bellwether
```

- [ ] **Step 2: Create `infra/prometheus/prometheus.yml`**

```yaml
global:
  scrape_interval: 5s

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
# Substrate services register here as they are built (Day 2+):
#  - job_name: campaign-service
#    static_configs: [{ targets: ["campaign-service:8000"] }]
```

- [ ] **Step 3: Create `infra/grafana/provisioning/datasources/prometheus.yml`**

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

- [ ] **Step 4: Verify the stack comes up**

Run: `docker compose up -d` then `docker compose ps`
Expected: postgres and redis `healthy`; prometheus and grafana `running`.

Run: `curl -s http://localhost:9090/-/ready`
Expected: `Prometheus Server is Ready.`

Run: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health`
Expected: `200`

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml infra
git commit -m "feat: infra skeleton - postgres, redis, prometheus, grafana via compose"
```

---

### Task 4: HTML running doc v1 (`docs/site/`)

**Files:**
- Create: `docs/site/index.html`

**Interfaces:**
- Produces: the living project explainer. Later days append per-level `<section>` blocks and update the day tracker — structure defined here is stable.

- [ ] **Step 1: Create `docs/site/index.html`**

Single self-contained file. Requirements (executor writes the HTML; all diagram code below is mandatory and verbatim):

**Head:** `<title>BELLWETHER — Build Log</title>`; Mermaid via CDN:
```html
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({ startOnLoad: true, theme: "dark" });
</script>
```

**Styling (inline `<style>`):** dark theme — background `#0b0b0f`, text `#e5e5e5`, accent red `#e50914` for headings/borders, system font stack, max-width 960px centered, cards with `border: 1px solid #2a2a33; border-radius: 12px; padding: 1.25rem`. Tables full-width with subtle row borders. Keep total CSS under ~60 lines.

**Body sections, in order:**

1. **Hero:** `BELLWETHER` title, tagline "An AI-native engineering platform for ad-tech teams — built in public, 30 days.", the not-affiliated disclaimer in small muted text, links: GitHub repo (placeholder `#`), video series (placeholder `#`), job posting ref "Target role: Netflix Staff AI Engineer — AI Foundation & Tooling (AJRT30201)".

2. **Day tracker:** table with columns Day | Level | Deliverable | Status. Pre-fill all 30 rows from spec section 5 tables. Day 1 status: `✅ shipped`; all others `⬜ planned`.

3. **System overview:** heading + prose (2 sentences: substrate vs bellwether) + this diagram verbatim in `<pre class="mermaid">`:

```
flowchart TB
    subgraph BELLWETHER["BELLWETHER — AI Foundation"]
        CTX["Context Layer<br/>RAG + Knowledge Graph + MCP"]
        DEV["Dev Lifecycle Agents"]
        OPS["Ops Agents"]
        ORCH["Multi-Agent Orchestrator"]
        EVAL["Eval Harness + Dashboard"]
        LLM["LLM Abstraction + Cost Tracking"]
    end
    subgraph SUB["SUBSTRATE — Mini Ads Platform"]
        CS["campaign-service"]
        ADS["ad-decision-service"]
        EV["event-service"]
        SIM["traffic-simulator + failure injection"]
        OBS["Prometheus + Grafana + JSON logs"]
    end
    CTX --> DEV & OPS
    ORCH --> DEV & OPS
    DEV & OPS --> LLM
    EVAL -.evaluates.-> CTX & DEV & OPS & ORCH
    SIM --> ADS
    ADS --> CS & EV
    SUB --> OBS
    OBS --> OPS
    SUB -. "code, docs, logs, metrics" .-> CTX
```

4. **Level 0 section** (`<section id="level-0">`): heading "Level 0 — The Substrate (Days 1–5)", goal paragraph, and two diagrams verbatim:

Ad-request sequence:
```
sequenceDiagram
    participant SIM as traffic-simulator
    participant ADS as ad-decision-service
    participant CS as campaign-service
    participant R as Redis
    participant EV as event-service
    SIM->>ADS: POST /ad-request {member_ctx, slot}
    ADS->>CS: eligible campaigns (targeting)
    ADS->>R: frequency cap check
    ADS->>ADS: brand-safety + budget pacing
    ADS-->>SIM: selected ad
    SIM->>EV: impression / click events
    EV-->>EV: aggregate, emit metrics
```

Day 1 infrastructure:
```
flowchart LR
    DEVBOX["Developer machine<br/>uv + pytest + ruff + mypy"] -->|docker compose up| NET["bellwether network"]
    NET --> PG[("Postgres 16")]
    NET --> RD[("Redis 7")]
    NET --> PROM["Prometheus :9090"]
    NET --> GRAF["Grafana :3000"]
    PROM --> GRAF
```

5. **Decisions so far:** card list — one card per ADR (just ADR-0001 today: title + one-line decision + link to file on GitHub placeholder).

6. **Eval scoreboard:** heading + placeholder table (Level | Metric | Target | Actual) pre-filled with the quality-gate rows from spec section 6, Actual column all `—`. Prose note: "Actuals publish as each level's gate runs."

7. **Footer:** "Built in public. Day 1 of 30." + disclaimer repeat.

- [ ] **Step 2: Verify rendering**

Run: open `docs/site/index.html` in a browser (`start docs\site\index.html` on Windows).
Expected: dark page, all 3 Mermaid diagrams render (requires internet for CDN), day tracker shows 30 rows.

- [ ] **Step 3: Commit**

```bash
git add docs/site
git commit -m "docs: HTML running doc v1 with system diagrams and 30-day tracker"
```

---

### Task 5: CI workflow + Day 1 devlog

**Files:**
- Create: `.github/workflows/ci.yml`, `docs/devlog/day-01.md`

**Interfaces:**
- Produces: CI pipeline all future PRs run through; devlog format for all future days

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --group dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy tests
      - run: uv run pytest
```

(Note: `mypy tests` expands to service packages on later days.)

- [ ] **Step 2: Create `docs/devlog/day-01.md`**

Format (used every day hereafter):

```markdown
# Day 1 — Foundation

**Level:** 0 · **Date:** <today's date>

## Shipped
- Repo scaffolding: uv, ruff (strict), mypy (strict), pytest, pre-commit
- README with architecture diagram and 30-day roadmap
- ADR-0001: Docker Compose over Kubernetes
- Infra skeleton: Postgres, Redis, Prometheus, Grafana — one command up
- Running doc v1: docs/site/index.html (system diagrams, day tracker, eval scoreboard)
- CI: lint + format + types + tests on every push/PR

## Decisions
- <bullet the day's ADRs / notable choices with one-line rationale>

## For the video
- <3-5 bullets: what to show on screen, in order>

## Tomorrow
- Day 2: campaign-service (CRUD, Postgres, tests, OpenAPI)
```

- [ ] **Step 3: Final verification**

Run: `uv run pytest && uv run ruff check . && uv run mypy tests`
Expected: all pass.

Run: `docker compose ps`
Expected: 4 services up.

- [ ] **Step 4: Commit**

```bash
git add .github docs/devlog
git commit -m "ci: quality pipeline; docs: day-01 devlog"
```

---

## Post-plan notes for the executor

- After Task 5, the user should create the GitHub repo (`bellwether`), add the remote, push, and enable GitHub Pages (deploy from `main`, `/docs` folder or Actions) — user action, not executor.
- Day 2 plan (campaign-service) is written fresh in the next session from spec section 3 — do not start Day 2 work from this plan.
