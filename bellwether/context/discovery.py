"""The corpus, as a manifest.

A crawl of "everything except an exclusion list" grows silently: add a directory and
the context layer quietly starts grounding agents in whatever landed there. An
ordered rule list shows up in a diff, and every stored document can name the rule
that admitted it. The exclusions below are a safety net under the rules, not the
mechanism (ADR-0006).
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
#
# `bellwether/**/*.py` is deliberately absent. The context layer does not ingest
# itself on Day 6 — the corpus is Level 0's output, and self-ingestion should be a
# decision taken at the knowledge graph, not a side effect of a glob.
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

EXCLUDED_FILENAMES = frozenset({"uv.lock"})

# A form, not a decision — grounding an agent in the template teaches it blanks.
EXCLUDED_PATHS = frozenset({"docs/adr/0000-adr-template.md"})


def _is_excluded(source_path: str) -> bool:
    """True if the path, its name, or any directory above it is excluded."""
    if source_path in EXCLUDED_PATHS:
        return True
    segments = source_path.split("/")
    if segments[-1] in EXCLUDED_FILENAMES:
        return True
    return any(segment in EXCLUDED_DIRECTORIES for segment in segments[:-1])


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
