# ADR-0001: Docker Compose over Kubernetes

**Date:** 2026-07-20
**Status:** Accepted

## Context
The BELLWETHER substrate is four small services plus an observability stack, run by one developer on one machine and demoed in daily videos. Netflix-style architecture signals matter to this project — but so does Staff-level pragmatism: choosing infrastructure proportionate to the problem is itself the signal.

## Decision
Docker Compose orchestrates all local infrastructure and services. No Kubernetes anywhere in the project.

## Alternatives considered
- **Kubernetes (kind/minikube):** rejected. Adds operational overhead and slower demo cycles while proving nothing relevant — the target role is AI foundation engineering, not cluster operations.
- **Bare processes (no containers):** rejected. No service isolation, no one-command setup, and it breaks the "<10 minutes from fresh clone to running system" quality gate.

## Consequences
- One-command startup (`docker compose up -d`), trivially reproducible on camera and by anyone who clones the repo.
- Healthchecks, networks, and volumes are declared in one readable file.
- If the project ever needs multi-node scale, Compose service definitions translate cleanly to Kubernetes manifests.
