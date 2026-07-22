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
    assert "docs/adr/0001-docker-compose-over-kubernetes.md" in adrs
    # The template is a form, not a decision. Grounding an agent in it would
    # teach it to answer questions with blanks.
    assert not any("0000-adr-template" in path for path in adrs)


def test_the_devlogs_and_the_level_0_runbook_are_in() -> None:
    grouped = _by_type(REPO_ROOT)
    assert len(grouped["devlog"]) >= 5
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
    (tmp_path / ".venv" / "substrate" / "vendored").mkdir(parents=True)
    (tmp_path / ".venv" / "substrate" / "vendored" / "lib.py").write_text("x = 3\n")
    (tmp_path / "data" / "context").mkdir(parents=True)
    (tmp_path / "data" / "context" / "corpus.jsonl").write_text("{}\n")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "uv.lock").write_text("locked\n")

    paths = [found.source_path for found in discover(tmp_path)]
    assert paths == ["substrate/campaign_service/main.py"]


def test_an_empty_tree_discovers_nothing(tmp_path: Path) -> None:
    assert discover(tmp_path) == []
