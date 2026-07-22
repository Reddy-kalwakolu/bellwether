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
    assert document.provenance.byte_size == len(b"one\ntwo\nthree\n")


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


def test_an_empty_document_has_no_lines() -> None:
    document = build_document("empty.md", "readme", "repo", "empty.md", "", NOW)
    assert document.provenance.line_count == 0
    assert document.provenance.byte_size == 0


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
