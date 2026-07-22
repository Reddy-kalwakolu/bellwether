"""The four service contracts, generated from the app objects themselves.

Deliberately not scraped from a running service. A corpus that depends on which
containers happen to be up is not reproducible, will not build in CI, and quietly
changes shape depending on the machine — which is the opposite of what a context
layer is for. Importing the app and calling `app.openapi()` gives the same bytes on
every machine, including one with Docker stopped (ADR-0006).
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
