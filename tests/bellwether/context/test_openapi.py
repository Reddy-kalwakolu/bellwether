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


def test_generating_the_contracts_needs_nothing_running() -> None:
    # If this ever needs a live container, ingestion stops being reproducible
    # and CI stops being able to build the corpus at all.
    first = openapi_documents(NOW)
    second = openapi_documents(NOW)
    assert [document.content_hash for document in first] == [
        document.content_hash for document in second
    ]
