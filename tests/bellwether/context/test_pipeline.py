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

    assert report.by_source_type["adr"] >= 5
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
