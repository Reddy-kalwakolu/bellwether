# platform/

Tooling that operates on the running system rather than shipping inside it.

## `level0_gate.py`

The Level 0 quality gate. It answers the spec's Level 0 metric — *all services
healthy under simulator load; failure injection works* — with a number rather
than an opinion.

```bash
docker compose up -d --build
docker compose restart prometheus grafana   # provisioning is not hot-reloaded
python -m uv run python platform/level0_gate.py
```

Ten checks: five service health endpoints, one Prometheus scrape check, and one
per injectable failure mode. Exit code 0 only when all ten pass.

**It runs against the live stack and is deliberately not part of the hermetic test
suite** — a gate that could pass with the substrate switched off would prove
nothing. What *is* hermetic is its decision logic: `evaluate_scenario` is pure and
unit-tested in `tests/platform/test_level0_gate.py`, so the rules that decide a
pass are verified without infrastructure, and the live run only supplies numbers.

The gate leaves the platform healthy when it finishes: the `steady` scenario is
also the rollback for any configuration a failure mode changed.

> `platform/` is on `pythonpath` (see `pyproject.toml`) rather than being a Python
> package — `platform` is a standard-library module name, and shadowing it would
> break imports across the whole project.
