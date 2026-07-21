# ADS-NEXUS Coding Standards

These rules are checkable. The Level 2 PR pre-review agent enforces them; humans should too.

## Types & linting
- Type hints on all function signatures. `mypy --strict` must pass.
- `ruff check` and `ruff format` clean. Line length 100.

## Service endpoints
Every HTTP endpoint must have:
- Request and response Pydantic models (no bare dicts across the wire).
- A structured log line containing at minimum: `service`, `endpoint`, `latency_ms`.
- Error handling that returns typed error responses — never an unhandled bare 500.

## Tests
- Every endpoint has at least one happy-path test and one failure-path test.
- No test may depend on another test's state or execution order.
- Tests use ads-domain fixtures (realistic campaigns, creatives, member contexts).

## Naming
- Use ads-domain vocabulary: `campaign`, `creative`, `frequency_cap`, `pacing`, `brand_safety`,
  `ad_request`, `impression`. Never generic `item`, `thing`, `data`, `obj`.
- Services are nouns (`campaign-service`); agent modules are role names (`pr_reviewer`, `rca_agent`).

## Commits
- Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `ci:`), imperative mood.
- A commit should leave the repo green: tests, lint, and types all passing.
