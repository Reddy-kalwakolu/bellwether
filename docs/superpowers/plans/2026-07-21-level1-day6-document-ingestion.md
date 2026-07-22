# BELLWETHER Level 1 / Day 6 — Document ingestion pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn everything Level 0 produced — Python source, five ADRs, five devlogs, the runbook, the standards, the design spec, the plans, the README, the substrate-gaps backlog, the Compose/Prometheus/Grafana config, and the live OpenAPI spec of all four services — into a hashed, provenance-carrying corpus behind a store protocol, so Day 7 can swap a vector store in underneath it without touching a line of ingestion code.

**Architecture:** Five small pure modules and one thin shell. `documents.py` defines the `Document` and its `Provenance` and owns normalisation and content hashing. `discovery.py` holds the corpus as an ordered list of **rules** — an explicit manifest of what belongs, not a crawl of everything that is not excluded. `loaders.py` turns one discovered file into one `Document`, extracting the title and the provenance attributes appropriate to its kind. `openapi.py` produces the four service specs **by importing the FastAPI app objects and calling `app.openapi()`** — no running container, no `curl`, so ingestion is reproducible in CI. `store.py` defines the `DocumentStore` protocol plus an in-memory and a JSONL implementation. `pipeline.py` runs discovery → load → hash → upsert → prune and returns a report; `__main__.py` prints it.

**The load-bearing idea:** the corpus is a **manifest, not a crawl**. A crawl of "everything minus an exclusion list" silently grows: add a directory, and the context layer starts grounding agents in whatever landed there. A rule list is reviewable in a diff, and every document in the store can name the rule that admitted it. The second load-bearing idea is that **content hashing is the unit of change**: re-ingesting an unchanged repo produces zero writes, which is what makes Day 7's embedding step affordable to re-run.

**Tech Stack:** Python 3.11+, Pydantic v2, pydantic-settings, FastAPI (for `app.openapi()` only), pytest. No new dependencies.

## Global Constraints

- Python 3.11+; type hints on all functions; `mypy --strict` clean
- Ruff clean (line length 100); `ruff format --check` clean; conventional commits
- **New code lives under `bellwether/context/`, which means adding `bellwether` to the mypy target in `pyproject.toml`'s reach and to the CI command.** CI runs `uv run mypy tests substrate platform` today; it becomes `uv run mypy tests substrate platform bellwether`
- **Reproduce the CI command verbatim before pushing.** A superset run locally hides module-resolution failures (day-05 execution note 10)
- **Tests are hermetic**: the whole suite passes with Docker stopped. Ingestion opens no socket — the OpenAPI specs come from imported app objects, never from a running service
- **Every file is read with `encoding="utf-8"` explicitly.** Windows defaults to cp1252 and the plans and devlogs are full of `→`, `·`, and `—`
- **Line endings are normalised before hashing** (`\r\n` and `\r` → `\n`). A Windows checkout and a Linux CI runner must produce the same `content_hash` for the same file, or nothing downstream is reproducible
- Test helpers taking arbitrary overrides use `**overrides: object`, never `Any` (ANN401); fixtures are annotated with their concrete type
- `json.loads` returns `Any` — assign to an annotated local before returning it (day-04 execution note 4)
- Every directory under `tests/` needs an `__init__.py`
- `uv` is invoked as `python -m uv`
- Verification gates are run **unpiped**, so a failure cannot hide behind `tail`'s exit status
- The corpus is written to `data/context/corpus.jsonl` — `data/` is already in `.gitignore`, and discovery must exclude it so the pipeline can never ingest its own output
- Definition of done: `docs/site/index.html` updated (bar and footer to DAY 06, pod head to `6 shipped · 24 queued`, Day 5 segment gains `nohead`, Day 6 segment becomes shipped, tracker row 06 to SHIPPED, and a **new Level 1 section** inserted after the Level 0 one, renumbering the SEQ eyebrows below it), and `docs/devlog/day-06.md` written

## File Structure

| File | Responsibility |
|---|---|
| `bellwether/__init__.py` | Package marker for the AI foundation |
| `bellwether/context/__init__.py` | Package marker for the context layer |
| `bellwether/context/config.py` | `CONTEXT_*` settings — repo root, corpus path |
| `bellwether/context/documents.py` | `Document`, `Provenance`, normalisation, content hashing — pure |
| `bellwether/context/discovery.py` | The corpus manifest: rules, exclusions, `discover()` — pure |
| `bellwether/context/loaders.py` | One discovered file → one `Document`, with title and provenance |
| `bellwether/context/openapi.py` | Four service specs from imported app objects — hermetic |
| `bellwether/context/store.py` | `DocumentStore` protocol, in-memory and JSONL implementations |
| `bellwether/context/pipeline.py` | discover → load → upsert → prune, returning `IngestionReport` |
| `bellwether/context/__main__.py` | `python -m bellwether.context` — runs the pipeline and prints the report |

---

### Task 1: The document and its provenance

**Files:**
- Create: `bellwether/__init__.py`, `bellwether/context/__init__.py`, `bellwether/context/documents.py`
- Create: `tests/bellwether/__init__.py`, `tests/bellwether/context/__init__.py`, `tests/bellwether/context/test_documents.py`

**Interfaces:**
- Produces:
  - `documents.SourceType` — `Literal["code","adr","devlog","runbook","standards","spec","plan","readme","backlog","config","openapi"]`
  - `documents.normalize(text: str) -> str`
  - `documents.content_hash(text: str) -> str` — returns `"sha256:<hex>"`
  - `documents.Provenance` — Pydantic model: `source_path: str`, `source_type: SourceType`, `component: str`, `title: str`, `byte_size: int`, `line_count: int`, `generated: bool = False`, `attributes: dict[str, str] = {}`
  - `documents.Document` — Pydantic model: `doc_id: str`, `content: str`, `content_hash: str`, `provenance: Provenance`, `ingested_at: datetime`
  - `documents.build_document(source_path, source_type, component, title, content, ingested_at, *, generated=False, attributes=None) -> Document`

The `doc_id` is the repo-relative POSIX path. Not a UUID: a citation has to be something a human can paste into an editor, and Level 1's whole promise is that every agent answer names its source.

- [ ] **Step 1: Write the failing tests**

Create `tests/bellwether/__init__.py` and `tests/bellwether/context/__init__.py` (empty files).

Create `tests/bellwether/context/test_documents.py`:

```python
"""A document is content plus the provenance that makes it citable."""

from __future__ import annotations

from datetime import UTC, datetime

from bellwether.context.documents import build_document, content_hash, normalize

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def test_windows_and_unix_line_endings_normalize_to_the_same_text() -> None:
    assert normalize("a\r\nb\rc\n") == "a\nb\nc\n"


def test_the_same_content_hashes_the_same_on_either_platform() -> None:
    # The corpus is hashed on a Windows checkout and re-hashed in Linux CI.
    # If these differ, every downstream "unchanged" check is a lie.
    assert content_hash("# ADR\r\ntext\r\n") == content_hash("# ADR\ntext\n")


def test_a_hash_is_labelled_with_its_algorithm() -> None:
    digest = content_hash("anything")
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_different_content_hashes_differently() -> None:
    assert content_hash("campaign") != content_hash("creative")


def test_a_document_is_identified_by_its_repo_relative_path() -> None:
    document = build_document(
        source_path="docs/adr/0005-real-failures-not-mocked.md",
        source_type="adr",
        component="docs",
        title="ADR-0005 — Real failures, not mocked ones",
        content="body\n",
        ingested_at=NOW,
    )
    assert document.doc_id == "docs/adr/0005-real-failures-not-mocked.md"
    assert document.provenance.source_type == "adr"
    assert document.content_hash == content_hash("body\n")


def test_provenance_records_the_size_of_what_was_ingested() -> None:
    document = build_document(
        source_path="README.md",
        source_type="readme",
        component="repo",
        title="BELLWETHER",
        content="one\ntwo\nthree\n",
        ingested_at=NOW,
    )
    assert document.provenance.line_count == 3
    assert document.provenance.byte_size == len("one\ntwo\nthree\n".encode())


def test_a_documents_content_is_stored_normalized() -> None:
    document = build_document(
        source_path="x.md",
        source_type="readme",
        component="repo",
        title="x",
        content="a\r\nb\r\n",
        ingested_at=NOW,
    )
    assert document.content == "a\nb\n"


def test_generated_documents_say_so() -> None:
    document = build_document(
        source_path="openapi/campaign-service.json",
        source_type="openapi",
        component="campaign-service",
        title="campaign-service OpenAPI",
        content="{}",
        ingested_at=NOW,
        generated=True,
        attributes={"generator": "app.openapi()"},
    )
    assert document.provenance.generated is True
    assert document.provenance.attributes["generator"] == "app.openapi()"


def test_attributes_default_to_empty_rather_than_shared() -> None:
    first = build_document("a.md", "readme", "repo", "a", "a", NOW)
    second = build_document("b.md", "readme", "repo", "b", "b", NOW)
    first.provenance.attributes["day"] = "06"
    assert second.provenance.attributes == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m uv run pytest tests/bellwether -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether'`

- [ ] **Step 3: Implement the document model**

Create `bellwether/__init__.py`:

```python
"""BELLWETHER — the AI foundation that operates on the substrate."""
```

Create `bellwether/context/__init__.py`:

```python
"""The context layer: ingestion, retrieval, knowledge graph, MCP server."""
```

Create `bellwether/context/documents.py`:

```python
"""What the context layer stores, and what makes it citable.

A document carries its provenance because every downstream answer has to name
its source. An agent that says "the budget ceiling is $2,147" without pointing
at day-05's devlog is indistinguishable from one that guessed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal[
    "code",
    "adr",
    "devlog",
    "runbook",
    "standards",
    "spec",
    "plan",
    "readme",
    "backlog",
    "config",
    "openapi",
]


def normalize(text: str) -> str:
    """Collapse line endings so the same file hashes the same on every platform."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def content_hash(text: str) -> str:
    """A labelled digest of normalised content — the unit of change detection."""
    digest = hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


class Provenance(BaseModel):
    """Where a document came from, and what kind of thing it is."""

    source_path: str
    source_type: SourceType
    component: str
    title: str
    byte_size: int
    line_count: int
    generated: bool = False
    attributes: dict[str, str] = Field(default_factory=dict)


class Document(BaseModel):
    """One ingested unit of team knowledge."""

    doc_id: str
    content: str
    content_hash: str
    provenance: Provenance
    ingested_at: datetime


def build_document(
    source_path: str,
    source_type: SourceType,
    component: str,
    title: str,
    content: str,
    ingested_at: datetime,
    *,
    generated: bool = False,
    attributes: dict[str, str] | None = None,
) -> Document:
    """Assemble a document, normalising and hashing its content on the way in."""
    normalized = normalize(content)
    return Document(
        doc_id=source_path,
        content=normalized,
        content_hash=content_hash(normalized),
        provenance=Provenance(
            source_path=source_path,
            source_type=source_type,
            component=component,
            title=title,
            byte_size=len(normalized.encode("utf-8")),
            line_count=normalized.count("\n") + (0 if normalized.endswith("\n") else 1)
            if normalized
            else 0,
            generated=generated,
            attributes=dict(attributes or {}),
        ),
        ingested_at=ingested_at,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m uv run pytest tests/bellwether -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add bellwether tests/bellwether
git commit -m "feat: the context-layer document, its provenance, and platform-stable content hashing"
```

---

### Task 2: The corpus manifest

**Files:**
- Create: `bellwether/context/discovery.py`, `tests/bellwether/context/test_discovery.py`

**Interfaces:**
- Consumes: `documents.SourceType`
- Produces:
  - `discovery.CorpusRule` — frozen dataclass: `pattern: str`, `source_type: SourceType`
  - `discovery.CORPUS_RULES: tuple[CorpusRule, ...]`
  - `discovery.EXCLUDED_DIRECTORIES: frozenset[str]`, `discovery.EXCLUDED_PATHS: frozenset[str]`
  - `discovery.DiscoveredFile` — frozen dataclass: `path: Path`, `source_path: str`, `source_type: SourceType`
  - `discovery.discover(root: Path) -> list[DiscoveredFile]` — sorted by `source_path`, first matching rule wins

The rules, in order:

| Pattern | Source type |
|---|---|
| `README.md`, `platform/README.md` | `readme` |
| `substrate/**/*.py`, `platform/**/*.py` | `code` |
| `docs/adr/*.md` | `adr` |
| `docs/devlog/*.md` | `devlog` |
| `docs/runbooks/*.md` | `runbook` |
| `docs/standards/*.md` | `standards` |
| `docs/superpowers/specs/*.md` | `spec` |
| `docs/superpowers/plans/*.md` | `plan` |
| `docs/backlog/*.md` | `backlog` |
| `docker-compose.yml`, `infra/**/*.yml`, `infra/**/*.json` | `config` |

`bellwether/**/*.py` is deliberately absent. The context layer does not ingest itself on Day 6 — the corpus is Level 0's output, and self-ingestion is a decision for Day 9's knowledge graph, not a side effect of a glob.

- [ ] **Step 1: Write the failing tests**

Create `tests/bellwether/context/test_discovery.py`:

```python
"""What belongs in the corpus is a reviewable list, not whatever is on disk."""

from __future__ import annotations

from pathlib import Path

from bellwether.context.discovery import CORPUS_RULES, discover

REPO_ROOT = Path(__file__).resolve().parents[3]


def _by_type(root: Path) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for found in discover(root):
        grouped.setdefault(found.source_type, []).append(found.source_path)
    return grouped


def test_every_rule_names_a_source_type() -> None:
    assert CORPUS_RULES
    for rule in CORPUS_RULES:
        assert rule.pattern
        assert rule.source_type


def test_discovery_is_sorted_and_free_of_duplicates() -> None:
    paths = [found.source_path for found in discover(REPO_ROOT)]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths))


def test_the_five_adrs_are_in_and_the_template_is_not() -> None:
    adrs = _by_type(REPO_ROOT)["adr"]
    assert len(adrs) == 5
    assert "docs/adr/0001-docker-compose-over-kubernetes.md" in adrs
    # The template is a form, not a decision. Grounding an agent in it would
    # teach it to answer questions with blanks.
    assert not any("0000-adr-template" in path for path in adrs)


def test_the_five_devlogs_and_the_level_0_runbook_are_in() -> None:
    grouped = _by_type(REPO_ROOT)
    assert len(grouped["devlog"]) == 5
    assert grouped["runbook"] == ["docs/runbooks/level-0-substrate.md"]


def test_substrate_and_platform_source_are_in_but_tests_are_not() -> None:
    code = _by_type(REPO_ROOT)["code"]
    assert "substrate/campaign_service/main.py" in code
    assert "platform/level0_gate.py" in code
    assert not any(path.startswith("tests/") for path in code)


def test_the_context_layer_does_not_ingest_itself() -> None:
    paths = [found.source_path for found in discover(REPO_ROOT)]
    assert not any(path.startswith("bellwether/") for path in paths)


def test_the_observability_config_is_in() -> None:
    config = _by_type(REPO_ROOT)["config"]
    assert "docker-compose.yml" in config
    assert "infra/prometheus/prometheus.yml" in config
    assert "infra/grafana/provisioning/dashboards/ads-delivery.json" in config


def test_the_spec_the_plans_the_standards_and_the_backlog_are_in() -> None:
    grouped = _by_type(REPO_ROOT)
    assert grouped["spec"] == ["docs/superpowers/specs/2026-07-20-bellwether-design.md"]
    assert len(grouped["plan"]) >= 5
    assert grouped["standards"] == ["docs/standards/coding-standards.md"]
    assert grouped["backlog"] == ["docs/backlog/substrate-gaps.md"]
    assert "README.md" in grouped["readme"]


def test_caches_lockfiles_and_the_pipelines_own_output_are_excluded(tmp_path: Path) -> None:
    (tmp_path / "substrate" / "campaign_service").mkdir(parents=True)
    (tmp_path / "substrate" / "campaign_service" / "main.py").write_text("x = 1\n")
    (tmp_path / "substrate" / "__pycache__").mkdir()
    (tmp_path / "substrate" / "__pycache__" / "cached.py").write_text("x = 2\n")
    (tmp_path / ".venv" / "lib" / "substrate").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "substrate" / "vendored.py").write_text("x = 3\n")
    (tmp_path / "data" / "context").mkdir(parents=True)
    (tmp_path / "data" / "context" / "corpus.jsonl").write_text("{}\n")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "uv.lock").write_text("locked\n")

    paths = [found.source_path for found in discover(tmp_path)]
    assert paths == ["substrate/campaign_service/main.py"]


def test_an_empty_tree_discovers_nothing(tmp_path: Path) -> None:
    assert discover(tmp_path) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.context.discovery'`

- [ ] **Step 3: Implement discovery**

Create `bellwether/context/discovery.py`:

```python
"""The corpus, as a manifest.

A crawl of "everything except an exclusion list" grows silently: add a directory
and the context layer quietly starts grounding agents in whatever landed there.
An ordered rule list shows up in a diff, and every stored document can name the
rule that admitted it. The exclusions below are a safety net under the rules,
not the mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bellwether.context.documents import SourceType


@dataclass(frozen=True)
class CorpusRule:
    """One glob that admits files of one kind into the corpus."""

    pattern: str
    source_type: SourceType


@dataclass(frozen=True)
class DiscoveredFile:
    """A file the manifest admitted, with the rule's verdict attached."""

    path: Path
    source_path: str
    source_type: SourceType


# Order matters: the first rule to claim a path owns it.
CORPUS_RULES: tuple[CorpusRule, ...] = (
    CorpusRule("README.md", "readme"),
    CorpusRule("platform/README.md", "readme"),
    CorpusRule("substrate/**/*.py", "code"),
    CorpusRule("platform/**/*.py", "code"),
    CorpusRule("docs/adr/*.md", "adr"),
    CorpusRule("docs/devlog/*.md", "devlog"),
    CorpusRule("docs/runbooks/*.md", "runbook"),
    CorpusRule("docs/standards/*.md", "standards"),
    CorpusRule("docs/superpowers/specs/*.md", "spec"),
    CorpusRule("docs/superpowers/plans/*.md", "plan"),
    CorpusRule("docs/backlog/*.md", "backlog"),
    CorpusRule("docker-compose.yml", "config"),
    CorpusRule("infra/**/*.yml", "config"),
    CorpusRule("infra/**/*.json", "config"),
)

# `data/` holds the corpus itself. Ingesting your own output is how a pipeline
# starts hashing its own hashes.
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        ".uv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "htmlcov",
        "dist",
        "data",
    }
)

EXCLUDED_PATHS = frozenset(
    {
        "uv.lock",
        # A form, not a decision — grounding an agent in it teaches it blanks.
        "docs/adr/0000-adr-template.md",
    }
)


def _is_excluded(source_path: str) -> bool:
    """True if any path segment, or the path itself, is on an exclusion list."""
    if source_path in EXCLUDED_PATHS:
        return True
    segments = source_path.split("/")
    return any(segment in EXCLUDED_DIRECTORIES for segment in segments) or (
        segments[-1] in EXCLUDED_PATHS
    )


def discover(root: Path) -> list[DiscoveredFile]:
    """Every file the manifest admits under `root`, sorted and deduplicated."""
    claimed: dict[str, DiscoveredFile] = {}
    for rule in CORPUS_RULES:
        for path in root.glob(rule.pattern):
            if not path.is_file():
                continue
            source_path = path.relative_to(root).as_posix()
            if _is_excluded(source_path) or source_path in claimed:
                continue
            claimed[source_path] = DiscoveredFile(path, source_path, rule.source_type)
    return [claimed[key] for key in sorted(claimed)]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m uv run pytest tests/bellwether -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add bellwether/context/discovery.py tests/bellwether/context/test_discovery.py
git commit -m "feat: the corpus manifest — an ordered rule list, not a crawl"
```

---

### Task 3: Loaders — title, component, and kind-specific provenance

**Files:**
- Create: `bellwether/context/loaders.py`, `tests/bellwether/context/test_loaders.py`

**Interfaces:**
- Consumes: `discovery.DiscoveredFile`, `documents.build_document`
- Produces:
  - `loaders.component_for(source_path: str) -> str`
  - `loaders.title_for(source_path: str, source_type: SourceType, content: str) -> str`
  - `loaders.attributes_for(source_path: str, source_type: SourceType) -> dict[str, str]`
  - `loaders.load(found: DiscoveredFile, ingested_at: datetime) -> Document`

Rules:

- **component** — `substrate/campaign_service/...` → `campaign-service`; `substrate/shared/...` → `shared`; `platform/...` → `platform`; `docs/...` → `docs`; `infra/...` and `docker-compose.yml` → `infra`; anything else → `repo`
- **title** — markdown kinds take the first `# ` heading; code takes the first line of the module docstring; everything else takes the file name
- **attributes** — `adr` gets `adr_number`; `devlog` gets `day`; `plan` gets `day` when the filename carries one; `code` gets `module` (the dotted import path)

- [ ] **Step 1: Write the failing tests**

Create `tests/bellwether/context/test_loaders.py`:

```python
"""Turning a file into a document: what it is called, and what it belongs to."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bellwether.context.discovery import DiscoveredFile
from bellwether.context.loaders import attributes_for, component_for, load, title_for

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def test_a_substrate_module_belongs_to_its_service() -> None:
    assert component_for("substrate/campaign_service/main.py") == "campaign-service"
    assert component_for("substrate/ad_decision_service/decisioning.py") == "ad-decision-service"
    assert component_for("substrate/shared/logging.py") == "shared"


def test_everything_else_belongs_where_it_lives() -> None:
    assert component_for("platform/level0_gate.py") == "platform"
    assert component_for("docs/adr/0001-x.md") == "docs"
    assert component_for("infra/prometheus/prometheus.yml") == "infra"
    assert component_for("docker-compose.yml") == "infra"
    assert component_for("README.md") == "repo"


def test_a_markdown_title_is_its_first_heading() -> None:
    content = "\n\n# ADR-0005 — Real failures\n\n**Status:** Accepted\n"
    assert title_for("docs/adr/0005-x.md", "adr", content) == "ADR-0005 — Real failures"


def test_markdown_with_no_heading_falls_back_to_its_filename() -> None:
    assert title_for("docs/devlog/day-09.md", "devlog", "no heading here\n") == "day-09.md"


def test_a_module_title_is_the_first_line_of_its_docstring() -> None:
    content = '"""One tick of the simulation.\n\nMore detail here.\n"""\n\nX = 1\n'
    assert title_for("substrate/x/driver.py", "code", content) == "One tick of the simulation."


def test_a_module_with_no_docstring_falls_back_to_its_filename() -> None:
    assert title_for("substrate/x/__init__.py", "code", "") == "__init__.py"


def test_unparseable_python_still_gets_a_title() -> None:
    # A half-written file must not take the whole pipeline down.
    assert title_for("substrate/x/broken.py", "code", "def (:\n") == "broken.py"


def test_an_adr_carries_its_number() -> None:
    assert attributes_for("docs/adr/0005-real-failures-not-mocked.md", "adr") == {
        "adr_number": "0005"
    }


def test_a_devlog_carries_its_day() -> None:
    assert attributes_for("docs/devlog/day-05.md", "devlog") == {"day": "05"}


def test_a_plan_carries_the_day_it_planned() -> None:
    attributes = attributes_for("docs/superpowers/plans/2026-07-21-level0-day5-sim.md", "plan")
    assert attributes == {"day": "05"}


def test_a_module_carries_its_import_path() -> None:
    assert attributes_for("substrate/campaign_service/main.py", "code") == {
        "module": "substrate.campaign_service.main"
    }


def test_config_carries_no_attributes() -> None:
    assert attributes_for("docker-compose.yml", "config") == {}


def test_loading_a_file_produces_a_hashed_document(tmp_path: Path) -> None:
    path = tmp_path / "day-06.md"
    path.write_text("# Day 6 — ingestion\n\nbody\n", encoding="utf-8")
    found = DiscoveredFile(path=path, source_path="docs/devlog/day-06.md", source_type="devlog")

    document = load(found, NOW)

    assert document.doc_id == "docs/devlog/day-06.md"
    assert document.provenance.title == "Day 6 — ingestion"
    assert document.provenance.component == "docs"
    assert document.provenance.attributes == {"day": "06"}
    assert document.content_hash.startswith("sha256:")
    assert document.ingested_at == NOW


def test_loading_reads_utf8_regardless_of_the_platform_default(tmp_path: Path) -> None:
    path = tmp_path / "spec.md"
    path.write_text("# Spec\n\nfill rate 43% → 2%, budget · pacing\n", encoding="utf-8")
    found = DiscoveredFile(path=path, source_path="docs/x/spec.md", source_type="spec")

    assert "→" in load(found, NOW).content
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/test_loaders.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.context.loaders'`

- [ ] **Step 3: Implement the loaders**

Create `bellwether/context/loaders.py`:

```python
"""One file in, one document out — with the provenance its kind deserves.

The title and the component are what a citation will show a human, so they are
extracted rather than guessed: an ADR's title is its heading, a module's title
is the first line of its docstring, and a substrate module belongs to the
service that would page you if it broke.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime

from bellwether.context.discovery import DiscoveredFile
from bellwether.context.documents import Document, SourceType, build_document, normalize

MARKDOWN_TYPES: frozenset[str] = frozenset(
    {"adr", "devlog", "runbook", "standards", "spec", "plan", "readme", "backlog"}
)

_ADR_NUMBER = re.compile(r"^(\d{4})-")
_DAY_NUMBER = re.compile(r"day-?(\d{1,2})")


def component_for(source_path: str) -> str:
    """Which part of the system a document describes."""
    segments = source_path.split("/")
    if segments[0] == "substrate" and len(segments) > 2:
        return segments[1].replace("_", "-")
    if segments[0] in {"platform", "docs", "infra"}:
        return segments[0]
    if source_path == "docker-compose.yml":
        return "infra"
    return "repo"


def _first_heading(content: str) -> str | None:
    """The first level-one markdown heading, if the document has one."""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _module_summary(content: str) -> str | None:
    """The first line of a module docstring, if it parses and has one."""
    try:
        docstring = ast.get_docstring(ast.parse(content))
    except SyntaxError:
        return None
    if not docstring:
        return None
    return docstring.strip().splitlines()[0].strip() or None


def title_for(source_path: str, source_type: SourceType, content: str) -> str:
    """A human-readable name for the document, for use in a citation."""
    filename = source_path.rsplit("/", 1)[-1]
    if source_type in MARKDOWN_TYPES:
        return _first_heading(content) or filename
    if source_type == "code":
        return _module_summary(content) or filename
    return filename


def attributes_for(source_path: str, source_type: SourceType) -> dict[str, str]:
    """The extra provenance fields that only this kind of document has."""
    filename = source_path.rsplit("/", 1)[-1]
    if source_type == "adr":
        match = _ADR_NUMBER.match(filename)
        return {"adr_number": match.group(1)} if match else {}
    if source_type in {"devlog", "plan"}:
        match = _DAY_NUMBER.search(filename)
        return {"day": match.group(1).zfill(2)} if match else {}
    if source_type == "code":
        return {"module": source_path.removesuffix(".py").replace("/", ".")}
    return {}


def load(found: DiscoveredFile, ingested_at: datetime) -> Document:
    """Read one discovered file and turn it into a document."""
    content = normalize(found.path.read_text(encoding="utf-8"))
    return build_document(
        source_path=found.source_path,
        source_type=found.source_type,
        component=component_for(found.source_path),
        title=title_for(found.source_path, found.source_type, content),
        content=content,
        ingested_at=ingested_at,
        attributes=attributes_for(found.source_path, found.source_type),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m uv run pytest tests/bellwether -v`
Expected: PASS (33 tests)

- [ ] **Step 5: Commit**

```bash
git add bellwether/context/loaders.py tests/bellwether/context/test_loaders.py
git commit -m "feat: loaders extract titles, components, and kind-specific provenance"
```

---

### Task 4: OpenAPI specs, generated not scraped

**Files:**
- Create: `bellwether/context/openapi.py`, `tests/bellwether/context/test_openapi.py`

**Interfaces:**
- Consumes: `documents.build_document`
- Produces:
  - `openapi.SERVICE_APPS: tuple[tuple[str, FastAPI], ...]` — `(service_name, app)` for all four services
  - `openapi.render_spec(app: FastAPI) -> str` — deterministic JSON text
  - `openapi.openapi_documents(ingested_at: datetime) -> list[Document]`

The specs come from **importing the app objects and calling `app.openapi()`**. Not from `curl http://localhost:8001/openapi.json`. A pipeline that needs a running container to produce its corpus cannot run in CI, cannot run on a laptop with Docker stopped, and produces a different corpus depending on which containers happened to be up. `json.dumps(..., sort_keys=True, indent=2)` makes the text stable, which makes the hash stable.

- [ ] **Step 1: Write the failing tests**

Create `tests/bellwether/context/test_openapi.py`:

```python
"""The API contracts, read out of the code rather than off the network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from bellwether.context.openapi import SERVICE_APPS, openapi_documents, render_spec

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def test_all_four_substrate_services_contribute_a_spec() -> None:
    assert {name for name, _ in SERVICE_APPS} == {
        "campaign-service",
        "ad-decision-service",
        "event-service",
        "traffic-simulator",
    }


def test_a_rendered_spec_is_valid_openapi_with_real_paths() -> None:
    apps = dict(SERVICE_APPS)
    spec: dict[str, Any] = json.loads(render_spec(apps["ad-decision-service"]))
    assert spec["openapi"].startswith("3.")
    assert "/ad-request" in spec["paths"]
    assert "/health" in spec["paths"]


def test_rendering_the_same_app_twice_is_byte_identical() -> None:
    # Determinism is the whole point: a spec that reorders itself between runs
    # would look like a change on every ingest and re-embed the entire corpus.
    apps = dict(SERVICE_APPS)
    assert render_spec(apps["campaign-service"]) == render_spec(apps["campaign-service"])


def test_specs_become_documents_marked_as_generated() -> None:
    documents = {document.doc_id: document for document in openapi_documents(NOW)}
    assert set(documents) == {
        "openapi/campaign-service.json",
        "openapi/ad-decision-service.json",
        "openapi/event-service.json",
        "openapi/traffic-simulator.json",
    }
    document = documents["openapi/event-service.json"]
    assert document.provenance.source_type == "openapi"
    assert document.provenance.component == "event-service"
    assert document.provenance.generated is True
    assert document.provenance.attributes["generator"] == "app.openapi()"
    assert document.content_hash.startswith("sha256:")


def test_generating_the_corpus_opens_no_socket() -> None:
    # If this ever needs a running container, ingestion stops being reproducible.
    first = openapi_documents(NOW)
    second = openapi_documents(NOW)
    assert [document.content_hash for document in first] == [
        document.content_hash for document in second
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/test_openapi.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.context.openapi'`

- [ ] **Step 3: Implement spec generation**

Create `bellwether/context/openapi.py`:

```python
"""The four service contracts, generated from the app objects themselves.

Deliberately not scraped from a running service. A corpus that depends on which
containers happen to be up is not reproducible, will not build in CI, and quietly
changes shape depending on the machine — which is the opposite of what a context
layer is for. Importing the app and calling `app.openapi()` gives the same bytes
on every machine, including one with Docker stopped.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import FastAPI

from bellwether.context.documents import Document, build_document
from substrate.ad_decision_service.main import app as ad_decision_app
from substrate.campaign_service.main import app as campaign_app
from substrate.event_service.main import app as event_app
from substrate.traffic_simulator.main import app as simulator_app

SERVICE_APPS: tuple[tuple[str, FastAPI], ...] = (
    ("ad-decision-service", ad_decision_app),
    ("campaign-service", campaign_app),
    ("event-service", event_app),
    ("traffic-simulator", simulator_app),
)


def render_spec(app: FastAPI) -> str:
    """The app's OpenAPI document as stable, sorted JSON text."""
    spec: dict[str, Any] = app.openapi()
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def openapi_documents(ingested_at: datetime) -> list[Document]:
    """One document per service contract, marked as generated."""
    return [
        build_document(
            source_path=f"openapi/{service_name}.json",
            source_type="openapi",
            component=service_name,
            title=f"{service_name} OpenAPI contract",
            content=render_spec(app),
            ingested_at=ingested_at,
            generated=True,
            attributes={"generator": "app.openapi()", "service": service_name},
        )
        for service_name, app in SERVICE_APPS
    ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m uv run pytest tests/bellwether -v`
Expected: PASS (38 tests)

- [ ] **Step 5: Commit**

```bash
git add bellwether/context/openapi.py tests/bellwether/context/test_openapi.py
git commit -m "feat: generate the four OpenAPI contracts from the app objects, hermetically"
```

---

### Task 5: The store protocol

**Files:**
- Create: `bellwether/context/store.py`, `tests/bellwether/context/test_store.py`

**Interfaces:**
- Consumes: `documents.Document`
- Produces:
  - `store.UpsertOutcome` — `StrEnum`: `ADDED`, `UPDATED`, `UNCHANGED`
  - `store.DocumentStore` — Protocol: `upsert(document) -> UpsertOutcome`, `get(doc_id) -> Document | None`, `documents() -> list[Document]`, `prune(keep: Collection[str]) -> list[str]`
  - `store.InMemoryDocumentStore` — the reference implementation
  - `store.JsonlDocumentStore(path: Path)` — same behaviour, backed by a JSONL file; `flush()` writes, `__init__` loads

`prune` is what keeps the store honest: delete a devlog from the repo and the next ingest removes it from the corpus rather than leaving an orphan that an agent will happily cite.

This protocol is the Day 7 seam. Chunking and embeddings arrive behind `upsert`; nothing in `pipeline.py` changes.

- [ ] **Step 1: Write the failing tests**

Create `tests/bellwether/context/test_store.py`:

```python
"""The store contract, and the two implementations that satisfy it."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bellwether.context.documents import build_document
from bellwether.context.store import (
    InMemoryDocumentStore,
    JsonlDocumentStore,
    UpsertOutcome,
)

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _document(source_path: str, content: str, ingested_at: datetime = NOW) -> object:
    return build_document(source_path, "adr", "docs", source_path, content, ingested_at)


def test_a_new_document_is_added() -> None:
    store = InMemoryDocumentStore()
    assert store.upsert(_document("a.md", "one")) is UpsertOutcome.ADDED
    assert len(store.documents()) == 1


def test_the_same_content_is_unchanged_and_keeps_its_first_ingest_time() -> None:
    store = InMemoryDocumentStore()
    store.upsert(_document("a.md", "one", NOW))

    assert store.upsert(_document("a.md", "one", LATER)) is UpsertOutcome.UNCHANGED
    stored = store.get("a.md")
    assert stored is not None
    # Provenance means "when did we first see this version", not "when did the
    # pipeline last run" — otherwise every run rewrites the whole corpus.
    assert stored.ingested_at == NOW


def test_changed_content_is_an_update_that_carries_the_new_time() -> None:
    store = InMemoryDocumentStore()
    store.upsert(_document("a.md", "one", NOW))

    assert store.upsert(_document("a.md", "two", LATER)) is UpsertOutcome.UPDATED
    stored = store.get("a.md")
    assert stored is not None
    assert stored.content == "two"
    assert stored.ingested_at == LATER


def test_an_unknown_document_is_none() -> None:
    assert InMemoryDocumentStore().get("nothing.md") is None


def test_documents_come_back_in_a_stable_order() -> None:
    store = InMemoryDocumentStore()
    for source_path in ("c.md", "a.md", "b.md"):
        store.upsert(_document(source_path, "x"))
    assert [document.doc_id for document in store.documents()] == ["a.md", "b.md", "c.md"]


def test_pruning_removes_what_the_repo_no_longer_has() -> None:
    store = InMemoryDocumentStore()
    store.upsert(_document("kept.md", "x"))
    store.upsert(_document("deleted.md", "x"))

    assert store.prune({"kept.md"}) == ["deleted.md"]
    assert [document.doc_id for document in store.documents()] == ["kept.md"]


def test_pruning_nothing_removes_nothing() -> None:
    store = InMemoryDocumentStore()
    store.upsert(_document("kept.md", "x"))
    assert store.prune({"kept.md"}) == []


def test_a_jsonl_store_survives_a_restart(tmp_path: Path) -> None:
    corpus = tmp_path / "context" / "corpus.jsonl"
    store = JsonlDocumentStore(corpus)
    store.upsert(_document("docs/adr/0001-x.md", "decision"))
    store.flush()

    reopened = JsonlDocumentStore(corpus)
    stored = reopened.get("docs/adr/0001-x.md")
    assert stored is not None
    assert stored.content == "decision"
    assert stored.provenance.source_type == "adr"


def test_reingesting_an_unchanged_corpus_writes_no_new_versions(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    store = JsonlDocumentStore(corpus)
    store.upsert(_document("a.md", "x", NOW))
    store.flush()

    reopened = JsonlDocumentStore(corpus)
    assert reopened.upsert(_document("a.md", "x", LATER)) is UpsertOutcome.UNCHANGED


def test_an_absent_corpus_file_is_an_empty_store(tmp_path: Path) -> None:
    assert JsonlDocumentStore(tmp_path / "missing.jsonl").documents() == []


def test_flushing_writes_one_json_object_per_line(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    store = JsonlDocumentStore(corpus)
    store.upsert(_document("a.md", "x"))
    store.upsert(_document("b.md", "y"))
    store.flush()

    lines = corpus.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert all(line.startswith("{") and line.endswith("}") for line in lines)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.context.store'`

- [ ] **Step 3: Implement the store**

Create `bellwether/context/store.py`:

```python
"""Where the corpus lives, behind a protocol.

The protocol is the point. Day 7 puts a vector store here — chunking and
embeddings arrive behind `upsert`, and the pipeline that fills it does not
change. Today's implementation is a JSONL file, which is a legitimate store for
a corpus this size and has the useful property of being readable in a diff.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from bellwether.context.documents import Document


class UpsertOutcome(StrEnum):
    """What an upsert did — the pipeline's report is a tally of these."""

    ADDED = "added"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class DocumentStore(Protocol):
    """Everything the ingestion pipeline needs from a corpus store."""

    def upsert(self, document: Document) -> UpsertOutcome:
        """Store a document, reporting whether it was new, changed, or identical."""
        ...

    def get(self, doc_id: str) -> Document | None:
        """One document by id, or None."""
        ...

    def documents(self) -> list[Document]:
        """Every stored document, ordered by id."""
        ...

    def prune(self, keep: Collection[str]) -> list[str]:
        """Delete everything whose id is not in `keep`; return what was removed."""
        ...


class InMemoryDocumentStore:
    """The reference implementation, and what the tests hold the protocol to."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}

    def upsert(self, document: Document) -> UpsertOutcome:
        """Store a document, reporting whether it was new, changed, or identical."""
        existing = self._documents.get(document.doc_id)
        if existing is None:
            self._documents[document.doc_id] = document
            return UpsertOutcome.ADDED
        if existing.content_hash == document.content_hash:
            return UpsertOutcome.UNCHANGED
        self._documents[document.doc_id] = document
        return UpsertOutcome.UPDATED

    def get(self, doc_id: str) -> Document | None:
        """One document by id, or None."""
        return self._documents.get(doc_id)

    def documents(self) -> list[Document]:
        """Every stored document, ordered by id."""
        return [self._documents[key] for key in sorted(self._documents)]

    def prune(self, keep: Collection[str]) -> list[str]:
        """Delete everything whose id is not in `keep`; return what was removed."""
        kept = set(keep)
        removed = sorted(doc_id for doc_id in self._documents if doc_id not in kept)
        for doc_id in removed:
            del self._documents[doc_id]
        return removed


class JsonlDocumentStore(InMemoryDocumentStore):
    """A corpus on disk: one JSON object per line, loaded on open."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    payload: dict[str, Any] = json.loads(line)
                    document = Document.model_validate(payload)
                    self._documents[document.doc_id] = document

    def flush(self) -> None:
        """Write the whole corpus out, ordered so the file diffs cleanly."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [document.model_dump_json() for document in self.documents()]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m uv run pytest tests/bellwether -v`
Expected: PASS (49 tests)

- [ ] **Step 5: Commit**

```bash
git add bellwether/context/store.py tests/bellwether/context/test_store.py
git commit -m "feat: document store protocol with in-memory and JSONL implementations"
```

---

### Task 6: The pipeline and its CLI

**Files:**
- Create: `bellwether/context/config.py`, `bellwether/context/pipeline.py`, `bellwether/context/__main__.py`
- Create: `tests/bellwether/context/test_pipeline.py`

**Interfaces:**
- Consumes: `discovery.discover`, `loaders.load`, `openapi.openapi_documents`, `store.DocumentStore`
- Produces:
  - `config.Settings` (env prefix `CONTEXT_`): `repo_root: Path`, `corpus_path: Path = Path("data/context/corpus.jsonl")`; module-level `settings`
  - `pipeline.IngestionReport` — frozen dataclass: `added: int`, `updated: int`, `unchanged: int`, `removed: list[str]`, `documents: int`, `bytes_ingested: int`, `by_source_type: dict[str, int]`
  - `pipeline.ingest(root: Path, store: DocumentStore, ingested_at: datetime | None = None, *, include_openapi: bool = True) -> IngestionReport`
  - `pipeline.format_report(report: IngestionReport) -> str`
  - `__main__.main(argv: Sequence[str] | None = None) -> int`

`include_openapi` exists so a test can point the pipeline at a synthetic tree without also getting four real service contracts in the result.

- [ ] **Step 1: Write the failing tests**

Create `tests/bellwether/context/test_pipeline.py`:

```python
"""The whole pipeline: discover, load, hash, store, prune."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bellwether.context.pipeline import format_report, ingest
from bellwether.context.store import InMemoryDocumentStore, JsonlDocumentStore

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _tiny_repo(root: Path) -> None:
    """A repo with one module, one ADR, one devlog, and one config file."""
    (root / "substrate" / "campaign_service").mkdir(parents=True)
    (root / "substrate" / "campaign_service" / "main.py").write_text(
        '"""campaign-service API."""\n', encoding="utf-8"
    )
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-compose.md").write_text("# ADR-0001\n", encoding="utf-8")
    (root / "docs" / "devlog").mkdir(parents=True)
    (root / "docs" / "devlog" / "day-01.md").write_text("# Day 1\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")


def test_a_first_ingest_adds_everything(tmp_path: Path) -> None:
    _tiny_repo(tmp_path)
    store = InMemoryDocumentStore()

    report = ingest(tmp_path, store, NOW, include_openapi=False)

    assert report.added == 4
    assert report.updated == 0
    assert report.unchanged == 0
    assert report.documents == 4
    assert report.by_source_type == {"adr": 1, "code": 1, "config": 1, "devlog": 1}
    assert report.bytes_ingested > 0


def test_reingesting_an_unchanged_repo_changes_nothing(tmp_path: Path) -> None:
    _tiny_repo(tmp_path)
    store = InMemoryDocumentStore()
    ingest(tmp_path, store, NOW, include_openapi=False)

    report = ingest(tmp_path, store, LATER, include_openapi=False)

    # This is what makes Day 7 affordable: nothing changed, so nothing re-embeds.
    assert (report.added, report.updated, report.unchanged) == (0, 0, 4)


def test_editing_a_file_produces_exactly_one_update(tmp_path: Path) -> None:
    _tiny_repo(tmp_path)
    store = InMemoryDocumentStore()
    ingest(tmp_path, store, NOW, include_openapi=False)

    (tmp_path / "docs" / "adr" / "0001-compose.md").write_text(
        "# ADR-0001\n\nSuperseded.\n", encoding="utf-8"
    )
    report = ingest(tmp_path, store, LATER, include_openapi=False)

    assert (report.added, report.updated, report.unchanged) == (0, 1, 3)
    stored = store.get("docs/adr/0001-compose.md")
    assert stored is not None
    assert "Superseded." in stored.content


def test_deleting_a_file_prunes_it_from_the_corpus(tmp_path: Path) -> None:
    _tiny_repo(tmp_path)
    store = InMemoryDocumentStore()
    ingest(tmp_path, store, NOW, include_openapi=False)

    (tmp_path / "docs" / "devlog" / "day-01.md").unlink()
    report = ingest(tmp_path, store, LATER, include_openapi=False)

    # An orphaned document is worse than a missing one: an agent will cite it.
    assert report.removed == ["docs/devlog/day-01.md"]
    assert store.get("docs/devlog/day-01.md") is None
    assert report.documents == 3


def test_the_openapi_contracts_join_the_corpus(tmp_path: Path) -> None:
    _tiny_repo(tmp_path)
    store = InMemoryDocumentStore()

    ingest(tmp_path, store, NOW)

    ids = {document.doc_id for document in store.documents()}
    assert "openapi/campaign-service.json" in ids
    assert "openapi/traffic-simulator.json" in ids


def test_ingesting_the_real_repo_produces_the_expected_corpus() -> None:
    store = InMemoryDocumentStore()

    report = ingest(REPO_ROOT, store)

    assert report.by_source_type["adr"] == 5
    assert report.by_source_type["devlog"] >= 5
    assert report.by_source_type["openapi"] == 4
    assert report.by_source_type["runbook"] == 1
    assert report.by_source_type["standards"] == 1
    assert report.by_source_type["spec"] == 1
    assert report.by_source_type["backlog"] == 1
    assert report.by_source_type["code"] > 20
    assert report.removed == []
    assert all(document.provenance.title for document in store.documents())


def test_ingesting_the_real_repo_twice_is_a_no_op() -> None:
    store = InMemoryDocumentStore()
    first = ingest(REPO_ROOT, store)
    second = ingest(REPO_ROOT, store)

    assert second.unchanged == first.documents
    assert (second.added, second.updated, second.removed) == (0, 0, [])


def test_the_corpus_round_trips_through_a_file(tmp_path: Path) -> None:
    _tiny_repo(tmp_path)
    corpus = tmp_path / "data" / "context" / "corpus.jsonl"
    store = JsonlDocumentStore(corpus)
    ingest(tmp_path, store, NOW, include_openapi=False)
    store.flush()

    reopened = JsonlDocumentStore(corpus)
    report = ingest(tmp_path, reopened, LATER, include_openapi=False)
    assert (report.added, report.updated, report.unchanged) == (0, 0, 4)


def test_the_report_reads_like_something_a_human_would_check(tmp_path: Path) -> None:
    _tiny_repo(tmp_path)
    store = InMemoryDocumentStore()

    text = format_report(ingest(tmp_path, store, NOW, include_openapi=False))

    assert "documents" in text
    assert "added" in text
    assert "adr" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m uv run pytest tests/bellwether/context/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.context.pipeline'`

- [ ] **Step 3: Implement config and pipeline**

Create `bellwether/context/config.py`:

```python
"""Runtime configuration for the context layer."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# bellwether/context/config.py -> bellwether/context -> bellwether -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Settings read from `CONTEXT_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="CONTEXT_")

    repo_root: Path = REPO_ROOT
    # `data/` is gitignored and excluded from discovery, so the pipeline can
    # never ingest its own output.
    corpus_path: Path = Path("data/context/corpus.jsonl")


settings = Settings()
```

Create `bellwether/context/pipeline.py`:

```python
"""discover → load → hash → upsert → prune.

The pipeline itself is boring on purpose. Every interesting decision lives in a
module it calls: what belongs in the corpus (discovery), what provenance a
document carries (loaders), where it goes (store). That is what lets Day 7 swap
a vector store in underneath without editing this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bellwether.context.discovery import discover
from bellwether.context.documents import Document
from bellwether.context.loaders import load
from bellwether.context.openapi import openapi_documents
from bellwether.context.store import DocumentStore, UpsertOutcome


@dataclass(frozen=True)
class IngestionReport:
    """What one run of the pipeline did."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: list[str] = field(default_factory=list)
    documents: int = 0
    bytes_ingested: int = 0
    by_source_type: dict[str, int] = field(default_factory=dict)


def ingest(
    root: Path,
    store: DocumentStore,
    ingested_at: datetime | None = None,
    *,
    include_openapi: bool = True,
) -> IngestionReport:
    """Ingest the corpus under `root` into `store`, returning what changed."""
    now = ingested_at or datetime.now(UTC)

    corpus: list[Document] = [load(found, now) for found in discover(root)]
    if include_openapi:
        corpus.extend(openapi_documents(now))
    corpus.sort(key=lambda document: document.doc_id)

    tally = {outcome: 0 for outcome in UpsertOutcome}
    by_source_type: dict[str, int] = {}
    bytes_ingested = 0
    for document in corpus:
        tally[store.upsert(document)] += 1
        source_type = document.provenance.source_type
        by_source_type[source_type] = by_source_type.get(source_type, 0) + 1
        bytes_ingested += document.provenance.byte_size

    removed = store.prune({document.doc_id for document in corpus})

    return IngestionReport(
        added=tally[UpsertOutcome.ADDED],
        updated=tally[UpsertOutcome.UPDATED],
        unchanged=tally[UpsertOutcome.UNCHANGED],
        removed=removed,
        documents=len(corpus),
        bytes_ingested=bytes_ingested,
        by_source_type=dict(sorted(by_source_type.items())),
    )


def format_report(report: IngestionReport) -> str:
    """The report as a human reads it — the line that goes in the video."""
    lines = [
        f"corpus:    {report.documents} documents, "
        f"{report.bytes_ingested / 1024:.1f} KiB",
        f"changes:   {report.added} added, {report.updated} updated, "
        f"{report.unchanged} unchanged, {len(report.removed)} removed",
        "by source type:",
    ]
    width = max((len(name) for name in report.by_source_type), default=0)
    lines.extend(
        f"  {name.ljust(width)}  {count:>4}" for name, count in report.by_source_type.items()
    )
    lines.extend(f"  removed: {doc_id}" for doc_id in report.removed)
    return "\n".join(lines)
```

- [ ] **Step 4: Implement the CLI**

Create `bellwether/context/__main__.py`:

```python
"""`python -m bellwether.context` — ingest the repo and print what changed."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from bellwether.context.config import settings
from bellwether.context.pipeline import format_report, ingest
from bellwether.context.store import JsonlDocumentStore


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ingestion pipeline against a repo root and write the corpus."""
    parser = argparse.ArgumentParser(description="Ingest the BELLWETHER corpus.")
    parser.add_argument("--root", type=Path, default=settings.repo_root)
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing the corpus.",
    )
    args = parser.parse_args(argv)

    root: Path = args.root
    corpus_path: Path = args.corpus or root / settings.corpus_path

    store = JsonlDocumentStore(corpus_path)
    report = ingest(root, store)
    if not args.dry_run:
        store.flush()

    print(format_report(report))
    print(f"corpus at: {corpus_path}" if not args.dry_run else "dry run: nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m uv run pytest tests/bellwether -v`
Expected: PASS (58 tests)

- [ ] **Step 6: Run the pipeline for real**

Run: `python -m uv run python -m bellwether.context`
Expected: a corpus report naming five ADRs, six devlogs, four OpenAPI contracts, and the code; `data/context/corpus.jsonl` written.

Then run it a second time and confirm every document reports `unchanged`.

- [ ] **Step 7: Commit**

```bash
git add bellwether/context tests/bellwether
git commit -m "feat: the ingestion pipeline and its CLI — discover, load, hash, store, prune"
```

---

### Task 7: Wire it into the gates, then documentation and definition of done

**Files:**
- Modify: `pyproject.toml`, `.github/workflows/ci.yml`
- Create: `docs/adr/0006-corpus-as-manifest.md`, `docs/devlog/day-06.md`
- Modify: `docs/site/index.html`

- [ ] **Step 1: Add `bellwether` to the type-check targets**

In `.github/workflows/ci.yml`, change the mypy step:

```yaml
      - run: uv run mypy tests substrate platform bellwether
```

New code under `bellwether/context/` is invisible to CI until it is on that line. Day 5's execution note 10 is the same failure from the other direction.

- [ ] **Step 2: Reproduce the CI command verbatim, unpiped**

```bash
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
python -m uv run pytest
```

Every one must be clean before the commit. Run them separately; do not chain them through a pipe.

- [ ] **Step 3: Write ADR-0006**

Create `docs/adr/0006-corpus-as-manifest.md` following the ADR template. **Decision:** the corpus is an ordered list of rules in code, and service contracts are generated by importing the FastAPI app objects — never crawled, never scraped from a running service. **Context:** the context layer grounds every agent from Level 2 onward; whatever is in it is what an agent will believe and cite. **Alternatives:** crawl-minus-exclusions (rejected — it grows silently, and a new directory changes what agents believe without anyone reviewing a diff); `curl http://localhost:8001/openapi.json` (rejected — the corpus would depend on which containers happened to be up, could not build in CI, and would differ between machines); committing the generated specs as files (rejected — a second copy of the contract that goes stale the moment a route changes). **Consequences:** adding a directory to the corpus is a code change with a test; ingestion imports the four service apps, so an import-time side effect in any service becomes an ingestion-time side effect. **Trigger:** if the corpus grows past what a rule list can describe — external documentation, tickets, Slack — a manifest file with the same semantics replaces the in-code tuple.

- [ ] **Step 4: Update the running doc**

In `docs/site/index.html`:
1. Bar `DAY <b>05</b>` → `DAY <b>06</b>`; footer `DAY 05 / 30` → `DAY 06 / 30`.
2. Pod head `5 shipped · 25 queued` → `6 shipped · 24 queued`.
3. Pod strip: add `nohead` to the Day 5 segment; change the Day 6 segment to `class="seg shipped"` and extend its title with ` · shipped`.
4. Tracker row `06` status cell → `<td class="st done">● SHIPPED</td>`.
5. Insert a **new Level 1 section** (`<section class="block" id="level-1">`, eyebrow `SEQ 03 · LEVEL 1 — THE CONTEXT LAYER (DAYS 6–10)`) immediately after the Level 0 section, containing the Day 6 narrative and an ingestion-pipeline diagram in the same plain-HTML style the Level 0 diagrams use (day-05's fix dropped the mermaid dependency for Level 0's own page — check what `docs/site/index.html` actually uses before choosing, and match it).
6. Renumber the eyebrows below: tracker `SEQ 03` → `SEQ 04`, decisions `SEQ 04` → `SEQ 05`, evals `SEQ 05` → `SEQ 06`.
7. Add an ADR-0006 card at the top of the decisions section.

- [ ] **Step 5: Write the devlog**

Create `docs/devlog/day-06.md` in the established format — `# Day 6 — Document ingestion pipeline`, `**Level:** 1 · **Date:** 2026-07-21`, then `## Shipped`, `## Decisions`, `## What running it found`, `## For the video`, `## Tomorrow`. The shot list covers: the corpus rule list read as a table; running `python -m bellwether.context` and reading the report; running it twice and watching everything come back `unchanged`; opening `corpus.jsonl` and reading one document's provenance block; showing that the OpenAPI contract came from `app.openapi()` with Docker stopped; and the store protocol as the Day 7 seam.

- [ ] **Step 6: Re-run every gate, unpiped**

```bash
python -m uv run pytest
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy tests substrate platform bellwether
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .github/workflows/ci.yml docs
git commit -m "docs: ADR-0006, day-06 devlog, Level 1 section on the running doc"
```

---

## Execution notes (what the plan missed)

Recorded during execution, so the next plan does not repeat these:

1. **Tests that count corpus contents must not assert the count the same day adds to it.** The plan's discovery test asserted exactly five ADRs and exactly five devlogs — and Task 7 of the same plan writes ADR-0006 and `day-06.md`. Asserting `>= 5` plus membership of a known path tests the rule without breaking on every future day. **Any test that counts documents in the real repo must use a floor, never an equality.**
2. **`dict.fromkeys(UpsertOutcome, 0)` over a dict comprehension.** Ruff's `C4`/`SIM` family flags `{k: 0 for k in ...}`; the plan's version would have failed lint. Not a correctness bug, but the plan should have written the idiom the linter already enforces elsewhere in this repo.
3. **The `line_count` expression in the plan was a nested conditional inside a keyword argument.** It parsed correctly but was unreadable and untestable in isolation. Extracted to `documents.line_count()` with its own test for the empty-string case.
4. **`EXCLUDED_PATHS` was doing two jobs.** The plan's `_is_excluded` checked the full path *and* the basename against one set, which silently made `uv.lock` a global filename exclusion and `docs/adr/0000-adr-template.md` a path exclusion using the same constant. Split into `EXCLUDED_FILENAMES` and `EXCLUDED_PATHS` so each has one meaning.
5. **Git's own CRLF warning is the confirmation the plan's hashing constraint was load-bearing.** The very first commit printed `LF will be replaced by CRLF the next time Git touches it` for every new file. Without normalisation before hashing, the Linux CI runner and a Windows checkout would disagree about all 67 documents on every run.
6. **Publishing a document count means re-publishing it after writing the ADR and the devlog.** The site and the devlog were written against `65 documents` and the last two files of the day made it 67. Numbers on the running doc are published evals — re-run the thing that produces them *after* the docs land, then update. It turned into the better demo anyway: the corpus ingesting the record of its own construction.
7. **`docs/site/*.html` is not in the corpus and that is deliberate.** The running doc is a rendering of what the ADRs, devlogs, and spec already say; ingesting it would give retrieval three copies of every claim and let the prettiest phrasing win. Worth stating explicitly the first time someone asks why the site is missing.
