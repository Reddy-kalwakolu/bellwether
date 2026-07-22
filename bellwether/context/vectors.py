"""Where the vectors live — one point per chunk, one named vector per engine.

Named vectors are the reason Qdrant replaced the spec's ChromaDB (ADR-0008). A chunk
of ADR-0005 is a single point carrying its Gemini vector, its Voyage vector, its potion
vector and its hashing vector at once. Switching engines is then a `using=` parameter
on the query — not a different collection, not a different client, and above all not a
different chunking run. A comparison where each engine sees different inputs is not a
comparison.

Reached over plain HTTP with httpx, which the project already depends on. A vector
database client library would be a second way to describe the same four calls.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from bellwether.context.chunking.models import Chunk
from bellwether.context.embedders.base import EngineSpec

COLLECTION = "bellwether_context"
# A stable namespace so a chunk_id always maps to the same Qdrant point id across
# runs and machines — Qdrant ids must be uint64 or UUID, and chunk ids are paths.
POINT_NAMESPACE = uuid.UUID("be11e70e-0000-4000-8000-000000000007")


class VectorStoreError(RuntimeError):
    """The vector store refused an operation."""


@dataclass(frozen=True)
class SearchHit:
    """One retrieved chunk, with the score that retrieved it."""

    chunk_id: str
    score: float
    text: str
    anchor: str | None
    source_path: str
    source_type: str
    component: str
    title: str


@dataclass(frozen=True)
class CollectionStats:
    """What the store currently holds."""

    points: int
    engines: dict[str, int] = field(default_factory=dict)


def point_id(chunk_id: str) -> str:
    """The deterministic Qdrant id for a chunk."""
    return str(uuid.uuid5(POINT_NAMESPACE, chunk_id))


def payload_for(chunk: Chunk) -> dict[str, Any]:
    """The provenance a hit carries back, so a citation survives retrieval."""
    provenance = chunk.provenance
    return {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "text": chunk.text,
        "anchor": provenance.anchor,
        "source_path": provenance.source_path,
        "source_type": provenance.source_type,
        "component": provenance.component,
        "title": provenance.title,
        "strategy": provenance.strategy,
    }


def _hit_from_payload(payload: dict[str, Any], score: float) -> SearchHit:
    """Rebuild a hit from a stored payload."""
    return SearchHit(
        chunk_id=str(payload.get("chunk_id", "")),
        score=score,
        text=str(payload.get("text", "")),
        anchor=payload.get("anchor"),
        source_path=str(payload.get("source_path", "")),
        source_type=str(payload.get("source_type", "")),
        component=str(payload.get("component", "")),
        title=str(payload.get("title", "")),
    )


class VectorStore(Protocol):
    """Everything the pipeline and the desk need from a vector store."""

    def ensure_collection(self, specs: Sequence[EngineSpec]) -> None:
        """Create the collection with one named vector per engine, idempotently."""
        ...

    def upsert(self, chunks: Sequence[Chunk], engine: str, vectors: Sequence[list[float]]) -> int:
        """Store one engine's vectors for these chunks; returns how many were written."""
        ...

    def search(
        self,
        engine: str,
        vector: list[float],
        limit: int = 5,
        source_types: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """The nearest chunks under one engine's vectors."""
        ...

    def stats(self) -> CollectionStats:
        """How many points, and how many vectors each engine has."""
        ...


class InMemoryVectorStore:
    """Cosine similarity over a dict. The test default, and the fallback when
    Qdrant is not running — a corpus this size does not strictly need a server."""

    def __init__(self) -> None:
        self._payloads: dict[str, dict[str, Any]] = {}
        self._vectors: dict[str, dict[str, list[float]]] = {}

    def ensure_collection(self, specs: Sequence[EngineSpec]) -> None:
        """Nothing to create; the dicts are the collection."""
        for spec in specs:
            self._vectors.setdefault(spec.name, {})

    def upsert(self, chunks: Sequence[Chunk], engine: str, vectors: Sequence[list[float]]) -> int:
        """Store one engine's vectors for these chunks; returns how many were written."""
        if len(chunks) != len(vectors):
            raise VectorStoreError(f"{len(chunks)} chunks but {len(vectors)} vectors for {engine}")
        slot = self._vectors.setdefault(engine, {})
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._payloads[chunk.chunk_id] = payload_for(chunk)
            slot[chunk.chunk_id] = vector
        return len(chunks)

    def search(
        self,
        engine: str,
        vector: list[float],
        limit: int = 5,
        source_types: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """The nearest chunks under one engine's vectors."""
        wanted = set(source_types or ())
        scored: list[tuple[float, str]] = []
        for chunk_id, stored in self._vectors.get(engine, {}).items():
            payload = self._payloads.get(chunk_id, {})
            if wanted and payload.get("source_type") not in wanted:
                continue
            scored.append((_cosine(vector, stored), chunk_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            _hit_from_payload(self._payloads[chunk_id], score) for score, chunk_id in scored[:limit]
        ]

    def stats(self) -> CollectionStats:
        """How many points, and how many vectors each engine has."""
        return CollectionStats(
            points=len(self._payloads),
            engines={engine: len(stored) for engine, stored in sorted(self._vectors.items())},
        )


def _cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity, tolerant of unnormalised input."""
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class HttpRequest(Protocol):
    """The seam the Qdrant store is tested through."""

    def __call__(
        self, method: str, url: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        """Send a request, returning the status code and the decoded body."""
        ...


def httpx_request(timeout_seconds: float = 30.0) -> HttpRequest:
    """The real transport. Imported lazily so tests never construct a client."""
    import httpx

    def request(
        method: str, url: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        response = httpx.request(method, url, json=payload, timeout=timeout_seconds)
        try:
            body: dict[str, Any] = response.json()
        except ValueError:
            body = {"status": response.text[:500]}
        return response.status_code, body

    return request


class QdrantVectorStore:
    """Qdrant over its REST API — four calls, no client library."""

    def __init__(
        self,
        base_url: str = "http://localhost:6333",
        collection: str = COLLECTION,
        request: HttpRequest | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._collection = collection
        self._request = request or httpx_request()

    def _call(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One Qdrant call, raising a typed error on anything but success."""
        status, body = self._request(method, f"{self._base_url}{path}", payload)
        if status >= 400:
            raise VectorStoreError(f"qdrant {method} {path} returned {status}: {body}")
        return body

    def ensure_collection(self, specs: Sequence[EngineSpec]) -> None:
        """Create the collection with one named vector per engine, idempotently."""
        status, _ = self._request("GET", f"{self._base_url}/collections/{self._collection}", None)
        if status == 200:
            return
        self._call(
            "PUT",
            f"/collections/{self._collection}",
            {
                "vectors": {
                    spec.name: {"size": spec.dimensions, "distance": "Cosine"} for spec in specs
                }
            },
        )

    def _existing(self, ids: list[str]) -> set[str]:
        """Which of these points Qdrant already holds."""
        body = self._call(
            "POST",
            f"/collections/{self._collection}/points",
            {"ids": ids, "with_payload": False, "with_vector": False},
        )
        return {str(point["id"]) for point in body.get("result", [])}

    def upsert(self, chunks: Sequence[Chunk], engine: str, vectors: Sequence[list[float]]) -> int:
        """Store one engine's vectors for these chunks; returns how many were written.

        The write path depends on whether the point already exists, and getting this
        wrong is silent and destructive. `PUT /points` **replaces** a point outright,
        so using it for the second engine wipes the first engine's vector — verified
        against a real Qdrant, not assumed. But `PUT /points/vectors` cannot create a
        point, so the first engine has no other option. Hence: create new points with
        their payload and this engine's vector, and extend existing ones through the
        merge-only endpoints.
        """
        if len(chunks) != len(vectors):
            raise VectorStoreError(f"{len(chunks)} chunks but {len(vectors)} vectors for {engine}")
        if not chunks:
            return 0

        paired = list(zip(chunks, vectors, strict=True))
        existing = self._existing([point_id(chunk.chunk_id) for chunk in chunks])
        fresh = [pair for pair in paired if point_id(pair[0].chunk_id) not in existing]
        known = [pair for pair in paired if point_id(pair[0].chunk_id) in existing]

        if fresh:
            self._call(
                "PUT",
                f"/collections/{self._collection}/points?wait=true",
                {
                    "points": [
                        {
                            "id": point_id(chunk.chunk_id),
                            "vector": {engine: list(vector)},
                            "payload": payload_for(chunk),
                        }
                        for chunk, vector in fresh
                    ]
                },
            )

        if known:
            self._call(
                "PUT",
                f"/collections/{self._collection}/points/vectors?wait=true",
                {
                    "points": [
                        {"id": point_id(chunk.chunk_id), "vector": {engine: list(vector)}}
                        for chunk, vector in known
                    ]
                },
            )
        return len(chunks)

    def drop_collection(self) -> None:
        """Delete the collection. The rebuild path when chunk text has changed.

        Payload is written when a point is created and not rewritten by later engines,
        because within one run every engine sees the same chunks and the same payload.
        A chunk whose *text* changed keeps its `chunk_id`, so the honest way to pick
        that up is to rebuild rather than to guess — which is what `--rebuild` does.
        """
        self._request("DELETE", f"{self._base_url}/collections/{self._collection}", None)

    def search(
        self,
        engine: str,
        vector: list[float],
        limit: int = 5,
        source_types: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """The nearest chunks under one engine's vectors."""
        query: dict[str, Any] = {
            "query": list(vector),
            "using": engine,
            "limit": limit,
            "with_payload": True,
        }
        if source_types:
            query["filter"] = {
                "must": [{"key": "source_type", "match": {"any": list(source_types)}}]
            }
        body = self._call("POST", f"/collections/{self._collection}/points/query", query)
        points = body.get("result", {}).get("points", [])
        return [
            _hit_from_payload(point.get("payload", {}), float(point.get("score", 0.0)))
            for point in points
        ]

    def stats(self) -> CollectionStats:
        """How many points, and how many vectors each engine actually has.

        Counted per engine with a `has_vector` filter rather than assumed from the
        collection config — a declared named vector and a populated one are different
        things, and the desk would happily show a column of confident zeros as ones.
        """
        body = self._call("GET", f"/collections/{self._collection}")
        result = body.get("result", {})
        configured = result.get("config", {}).get("params", {}).get("vectors", {})

        engines: dict[str, int] = {}
        for name in sorted(configured):
            counted = self._call(
                "POST",
                f"/collections/{self._collection}/points/count",
                {"filter": {"must": [{"has_vector": name}]}, "exact": True},
            )
            engines[name] = int(counted.get("result", {}).get("count") or 0)

        return CollectionStats(points=int(result.get("points_count") or 0), engines=engines)
