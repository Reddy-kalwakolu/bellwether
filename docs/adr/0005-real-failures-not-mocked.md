# ADR-0005: Failures are injected by changing real configuration, not by mocking

**Date:** 2026-07-21
**Status:** Accepted

## Context
The traffic-simulator has to be able to break the platform on demand — that is most of its value. Level 3's ops agents will be evaluated on whether they find the **right** root cause of a staged incident, and Day 20's quality gate puts a number on it.

That evaluation is only meaningful if the incident has a real cause. If a failure is produced by a mock, the only discoverable cause *is* the mock, and an agent that "finds" it has learned nothing about the system. The eval would measure the fixture, not the agent.

There is a second pull in the same direction. The spec's whole premise is that the substrate exists so the AI layer has something real to operate on — "no mock-data theater". A failure-injection mechanism that fakes symptoms would put mock data at the exact point where the project claims not to have any.

## Decision
Failure modes are injected by **changing real state through the same public APIs any other client would use.**

- `bad_config_deploy` genuinely `PATCH`es every campaign's targeting to a country the traffic never comes from. ad-decision-service then reads that configuration and correctly declines to fill. Nothing is faked; the decision path behaves perfectly and the fill rate still collapses.
- `budget_runaway` genuinely raises one campaign's daily budget, and pacing correctly stops throttling it.
- `error_burst` sends genuinely malformed ad requests, and the API correctly rejects them with the 422s its schema promises.
- `traffic_surge` genuinely raises the request rate.

No service contains a branch that knows it is being tested. There is no `CHAOS=true` flag, no debug endpoint, no mocked metric.

Because the changes are real, **recovery has to be real too.** The `steady` scenario is not merely "stop making it worse" — it restores the seed campaigns' targeting, budgets, and caps. Reverting an incident is a rollback, which is exactly the remediation a Level 3 guided-resolution agent would recommend.

## Alternatives considered
- **A `CHAOS=true` environment variable each service reads:** rejected. It puts test code on the serving path, and the "root cause" an ops agent would find is a branch in our own source rather than a configuration mistake — the least realistic and least interesting incident class.
- **A fault-injection proxy dropping or delaying packets:** rejected *for now*, not on principle. It produces failures with no configuration cause to find, which is the wrong shape for a config-driven ads platform where the realistic outage is a bad targeting deploy, not a severed cable.
- **Writing synthetic series straight into Prometheus:** rejected outright. Dashboards would show an incident that never happened. This is precisely the mock-data theater the project exists to avoid.
- **Mutating the database directly:** rejected. It would bypass validation and let the simulator create states the API forbids — states no real incident could reach, so no agent should be trained or evaluated on them.

## Consequences
- **Injecting a failure really breaks the running platform.** That is the point, and it means the simulator owns recovery. `steady` is a rollback, and the Level 0 gate leaves the platform healthy when it exits.
- **The seed campaign set is part of the product, not a test fixture.** Rollback is defined as "restore the seed configuration", so the seed set has to be realistic, present, and idempotent to re-apply.
- Injection is bounded by what the API allows. That is mostly a feature — it keeps every staged incident reachable in production — but it means a failure class with no configuration cause cannot be staged this way.
- Because the simulator drives real load against real validation, it finds real defects. On day one it found two: campaign budgets overflowing a 32-bit column, and that overflow surfacing as a bare 500 in violation of the project's own error standard.

**The trigger that extends this decision:** the first failure class we genuinely need that has no configuration cause — a network partition, a dependency timeout, a half-dead replica. That is when a fault-injection proxy earns its place **alongside** this mechanism, not instead of it.
