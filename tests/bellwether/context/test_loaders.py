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
