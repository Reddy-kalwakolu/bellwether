"""One point per chunk, one named vector per engine — and no server in the tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bellwether.context.chunking.models import Chunk, build_chunk
from bellwether.context.documents import build_document
from bellwether.context.embedders import engine_specs
from bellwether.context.vectors import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorStoreError,
    payload_for,
    point_id,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _chunk(chunk_index: int, text: str, source_type: str = "adr") -> Chunk:
    document = build_document(
        source_path=f"docs/adr/000{chunk_index}-x.md",
        source_type=source_type,  # type: ignore[arg-type]
        component="docs",
        title=f"ADR-000{chunk_index}",
        content=text,
        ingested_at=NOW,
    )
    return build_chunk(document, text, "markdown", chunk_index, f"ADR-000{chunk_index}", 1, 2)


class FakeRequest:
    """Answers Qdrant calls from a script, and records what was sent.

    Keyed by `METHOD path-fragment` so a scripted 404 on the existence check does not
    also answer the PUT that follows it.
    """

    def __init__(self, responses: dict[str, tuple[int, dict[str, Any]]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(
        self, method: str, url: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((method, url, payload))
        for key, response in self.responses.items():
            wanted_method, fragment = key.split(" ", 1)
            if method == wanted_method and fragment in url:
                return response
        return 200, {"result": {}}


# --- ids and payloads ----------------------------------------------------------


def test_a_chunk_always_maps_to_the_same_point_id() -> None:
    assert point_id("docs/adr/0005-x.md#0003") == point_id("docs/adr/0005-x.md#0003")
    assert point_id("a#0000") != point_id("b#0000")


def test_a_payload_carries_everything_a_citation_needs() -> None:
    payload = payload_for(_chunk(5, "the decision"))
    assert payload["chunk_id"].endswith("#0005")
    assert payload["anchor"] == "ADR-0005"
    assert payload["source_path"] == "docs/adr/0005-x.md"
    assert payload["text"] == "the decision"


# --- the in-memory store -------------------------------------------------------


def test_storing_and_finding_a_chunk() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection(engine_specs())
    written = store.upsert([_chunk(1, "budget pacing")], "hashing", [[1.0, 0.0]])

    assert written == 1
    [hit] = store.search("hashing", [1.0, 0.0], limit=5)
    assert hit.chunk_id.endswith("#0001")
    assert hit.anchor == "ADR-0001"
    assert hit.score > 0.99


def test_the_nearest_chunk_comes_back_first() -> None:
    store = InMemoryVectorStore()
    store.upsert(
        [_chunk(1, "near"), _chunk(2, "far")],
        "hashing",
        [[1.0, 0.0], [0.0, 1.0]],
    )
    hits = store.search("hashing", [0.9, 0.1], limit=2)
    assert hits[0].chunk_id.endswith("#0001")
    assert hits[0].score > hits[1].score


def test_every_engine_keeps_its_own_vectors_on_the_same_chunk() -> None:
    store = InMemoryVectorStore()
    chunk = _chunk(1, "shared")
    store.upsert([chunk], "hashing", [[1.0, 0.0]])
    store.upsert([chunk], "voyage", [[0.0, 1.0]])

    # One point, two named vectors — the whole reason for ADR-0008.
    assert store.stats().points == 1
    assert store.stats().engines["hashing"] == 1
    assert store.stats().engines["voyage"] == 1
    assert store.search("hashing", [1.0, 0.0])[0].score > 0.99
    assert store.search("voyage", [0.0, 1.0])[0].score > 0.99


def test_searching_can_be_narrowed_to_a_kind_of_document() -> None:
    store = InMemoryVectorStore()
    store.upsert(
        [_chunk(1, "an adr", "adr"), _chunk(2, "a devlog", "devlog")],
        "hashing",
        [[1.0, 0.0], [1.0, 0.0]],
    )
    hits = store.search("hashing", [1.0, 0.0], limit=5, source_types=["devlog"])
    assert [hit.source_type for hit in hits] == ["devlog"]


def test_mismatched_chunks_and_vectors_are_refused() -> None:
    with pytest.raises(VectorStoreError, match="1 chunks but 2 vectors"):
        InMemoryVectorStore().upsert([_chunk(1, "x")], "hashing", [[1.0], [2.0]])


def test_searching_an_engine_with_nothing_stored_is_empty_not_an_error() -> None:
    assert InMemoryVectorStore().search("gemini", [1.0, 0.0]) == []


# --- the qdrant store ----------------------------------------------------------


def test_the_collection_declares_one_named_vector_per_engine() -> None:
    request = FakeRequest({"GET collections/bellwether_context": (404, {})})
    QdrantVectorStore(request=request).ensure_collection(engine_specs())

    put = next(call for call in request.calls if call[0] == "PUT")
    vectors = put[2]["vectors"]
    assert set(vectors) == {"gemini", "voyage", "potion", "hashing"}
    assert vectors["gemini"] == {"size": 3072, "distance": "Cosine"}
    assert vectors["hashing"]["size"] == 256


def test_an_existing_collection_is_not_recreated() -> None:
    request = FakeRequest({"GET collections/bellwether_context": (200, {"result": {}})})
    QdrantVectorStore(request=request).ensure_collection(engine_specs())
    assert [call[0] for call in request.calls] == ["GET"]


def test_a_new_point_is_created_with_its_payload_and_its_vector_together() -> None:
    request = FakeRequest({"POST collections/bellwether_context/points": (200, {"result": []})})
    QdrantVectorStore(request=request).upsert([_chunk(1, "text")], "voyage", [[0.5, 0.5]])

    created = next(call for call in request.calls if call[0] == "PUT")
    point = created[2]["points"][0]
    assert point["vector"] == {"voyage": [0.5, 0.5]}
    assert point["payload"]["anchor"] == "ADR-0001"


def test_a_second_engine_never_replaces_the_point_it_only_adds_a_vector() -> None:
    chunk = _chunk(1, "text")
    request = FakeRequest(
        # The existence probe reports the point is already there.
        {
            "POST collections/bellwether_context/points": (
                200,
                {"result": [{"id": point_id(chunk.chunk_id)}]},
            )
        }
    )
    QdrantVectorStore(request=request).upsert([chunk], "gemini", [[0.1, 0.2]])

    # Verified against a real Qdrant: PUT /points REPLACES a point outright, so using
    # it here would silently wipe every other engine's vector and quietly turn the
    # comparison into a lie. Only the merge-only endpoint may be used.
    assert not any(call[1].endswith("/points?wait=true") for call in request.calls)
    added = next(call for call in request.calls if "/points/vectors" in call[1])
    assert added[2]["points"][0]["vector"] == {"gemini": [0.1, 0.2]}


def test_per_engine_counts_are_measured_not_assumed() -> None:
    request = FakeRequest(
        {
            "GET collections/bellwether_context": (
                200,
                {
                    "result": {
                        "points_count": 7,
                        "config": {"params": {"vectors": {"gemini": {}, "potion": {}}}},
                    }
                },
            ),
            "POST collections/bellwether_context/points/count": (200, {"result": {"count": 7}}),
        }
    )
    stats = QdrantVectorStore(request=request).stats()

    assert stats.points == 7
    # A declared named vector and a populated one are different things.
    counted = [call for call in request.calls if call[1].endswith("/points/count")]
    assert [call[2]["filter"]["must"][0]["has_vector"] for call in counted] == ["gemini", "potion"]


def test_search_passes_the_engine_as_the_named_vector() -> None:
    request = FakeRequest(
        {
            "POST points/query": (
                200,
                {
                    "result": {
                        "points": [
                            {
                                "score": 0.83,
                                "payload": {
                                    "chunk_id": "docs/adr/0005-x.md#0002",
                                    "text": "the decision",
                                    "anchor": "ADR-0005 › Decision",
                                    "source_path": "docs/adr/0005-x.md",
                                    "source_type": "adr",
                                    "component": "docs",
                                    "title": "ADR-0005",
                                },
                            }
                        ]
                    }
                },
            )
        }
    )
    hits = QdrantVectorStore(request=request).search("gemini", [0.1, 0.2], limit=3)

    _, _, payload = request.calls[0]
    assert payload["using"] == "gemini"
    assert payload["limit"] == 3
    assert payload["with_payload"] is True
    assert hits[0].anchor == "ADR-0005 › Decision"
    assert hits[0].score == 0.83


def test_a_filtered_search_sends_a_qdrant_filter() -> None:
    request = FakeRequest({"POST points/query": (200, {"result": {"points": []}})})
    QdrantVectorStore(request=request).search("gemini", [0.1], source_types=["adr", "runbook"])
    assert request.calls[0][2]["filter"]["must"][0]["match"]["any"] == ["adr", "runbook"]


def test_a_qdrant_failure_is_a_typed_error_naming_the_call() -> None:
    request = FakeRequest({"POST points/query": (500, {"status": "boom"})})
    with pytest.raises(VectorStoreError, match="qdrant POST .* returned 500"):
        QdrantVectorStore(request=request).search("gemini", [0.1])


def test_upserting_nothing_touches_no_endpoint() -> None:
    request = FakeRequest()
    assert QdrantVectorStore(request=request).upsert([], "gemini", []) == 0
    assert request.calls == []
