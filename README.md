# BELLWETHER

**An AI-native engineering platform for ad-tech teams — built in public.**

<!-- CI badge added after first push to GitHub -->

## What is this?

BELLWETHER is a two-part system. The **Substrate** is a mini Netflix-style ads platform — four Python microservices (campaign management, ad decisioning, event tracking, traffic simulation) producing real traffic, structured logs, Prometheus metrics, and stageable incidents. **Bellwether** itself is the star: an AI foundation layer that operates on the substrate — a centralized context layer (RAG + knowledge graph), development lifecycle agents (code gen, test gen, PR pre-review, deployment validation), operational intelligence agents (incident triage, root cause analysis, guided resolution), a multi-agent orchestrator, and an evaluation harness.

The thesis: **AI velocity with provable quality.** Agentic tooling is easy to demo and hard to trust. Every AI capability in this repo ships with numeric evaluations — retrieval relevance scores, seeded-bug catch rates, RCA accuracy — so speed never outruns accountability.

This project is being built in public over 30 days, one deliverable per day, with a daily video series. <!-- video series link TBA -->

## The name

A bellwether is the lead sheep in a flock — the one fitted with a bell, whose movement the rest follow, so the shepherd can tell where the flock is going without seeing it. The word later came to mean a leading indicator: the thing you watch to know what happens next.

Both meanings are the job. The first AI engineer on a team is the one who goes first and makes the path followable. And the eval scoreboard below is the bell — the signal that tells you whether the flock is moving somewhere good, before the whole team is standing there.

## Architecture

```mermaid
flowchart TB
    subgraph BELLWETHER["BELLWETHER — AI Foundation"]
        CTX["Context Layer<br/>(RAG + Knowledge Graph + MCP server)"]
        DEV["Dev Lifecycle Agents<br/>(code gen / test gen / PR review / deploy validation)"]
        OPS["Ops Agents<br/>(triage / RCA / guided resolution)"]
        ORCH["Multi-Agent Orchestrator<br/>(LangGraph, HITL gates)"]
        EVAL["Eval Harness + Dashboard"]
        LLM["LLM Abstraction<br/>(Claude API / Ollama, cost tracking)"]
    end
    subgraph SUB["SUBSTRATE — Mini Ads Platform"]
        CS["campaign-service"]
        ADS["ad-decision-service"]
        EV["event-service"]
        SIM["traffic-simulator<br/>(+ failure injection)"]
        OBS["Prometheus + Grafana<br/>structured JSON logs"]
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

## The 30-day roadmap

| Level | Days | Theme |
|---|---|---|
| 0 | 1–5 | The Substrate — mini ads platform, observability, failure-injecting traffic simulator |
| 1 | 6–10 | Context Layer — ingestion, embeddings, hybrid retrieval, AST knowledge graph, MCP server |
| 2 | 11–15 | Dev Lifecycle Agents — code gen, test gen + mutation testing, PR pre-review, deploy validation |
| 3 | 16–20 | Ops Agents — log intelligence, triage, RCA, guided resolution, self-healing loop |
| 4 | 21–25 | Orchestration — agent protocol, parallel execution, conflict resolution, HITL, Actor-Critic evals |
| 5 | 26–30 | Platform & Launch — AI-first dev env, CI/CD AI gates, eval dashboard, grand demo, launch |

## Status

**Day 1: Foundation** — Level 0 in progress.

- [x] Day 1 — repo scaffolding, ADR-0001, infra skeleton, running doc v1, CI
- [ ] Day 2 — campaign-service
- [ ] Day 3 — ad-decision-service
- [ ] Day 4 — event-service + observability
- [ ] Day 5 — traffic-simulator + failure injection

## Quickstart

```bash
uv sync --group dev      # install toolchain
uv run pytest            # run tests
docker compose up -d     # start infra (Postgres, Redis, Prometheus, Grafana)
```

## Docs

- [Design specification](docs/superpowers/specs/2026-07-20-bellwether-design.md)
- [Architecture Decision Records](docs/adr/)
- [Running doc (living explainer with diagrams)](docs/site/index.html)
- [Daily devlog](docs/devlog/)

## Disclaimer

BELLWETHER is an independent open-source project, not affiliated with or endorsed by Netflix.
