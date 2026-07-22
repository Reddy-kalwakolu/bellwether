"""The store contract, and the two implementations that satisfy it."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bellwether.context.documents import Document, build_document
from bellwether.context.store import InMemoryDocumentStore, JsonlDocumentStore, UpsertOutcome

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _document(source_path: str, content: str, ingested_at: datetime = NOW) -> Document:
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


def test_the_corpus_file_survives_non_ascii_content(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    store = JsonlDocumentStore(corpus)
    store.upsert(_document("spec.md", "fill rate 43% → 2% · pacing"))
    store.flush()

    stored = JsonlDocumentStore(corpus).get("spec.md")
    assert stored is not None
    assert "→" in stored.content
