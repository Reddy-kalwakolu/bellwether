"""event-service HTTP API.

Where the serving path becomes a durable record. Impressions and clicks land here,
are deduplicated by the caller's event id, and are aggregated on read into the
delivery numbers — impressions, clicks, CTR, spend — that the Grafana dashboards
and, three levels from now, the RCA agent both read.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from substrate.event_service.aggregation import delivery_for_campaign, delivery_rollup
from substrate.event_service.config import settings
from substrate.event_service.db import engine, get_session
from substrate.event_service.models import AdEvent, Base
from substrate.event_service.schemas import (
    AdEventCreate,
    AdEventRead,
    CampaignDelivery,
    ErrorResponse,
    EventAck,
)
from substrate.shared.logging import configure_logging, log_context
from substrate.shared.observability import install_request_observability

logger = logging.getLogger("event_service.api")

EVENTS = Counter(
    "ad_events_total",
    "Delivery events ingested, by type.",
    ["service", "event_type"],
)
DUPLICATES = Counter(
    "ad_events_duplicate_total",
    "Delivery events rejected because their event id was already stored.",
    ["service"],
)
SPEND = Counter(
    "ad_spend_micros_total",
    "Advertiser spend recorded from impressions, in micros.",
    ["service"],
)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid request"}
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure logging and ensure the schema exists before serving traffic."""
    configure_logging(settings.service_name)
    Base.metadata.create_all(engine)
    logger.info("event-service ready")
    yield


app = FastAPI(
    title="event-service",
    version="0.1.0",
    summary="Idempotent impression and click ingestion, and delivery aggregation.",
    lifespan=lifespan,
)


install_request_observability(app, settings.service_name, logger)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    """Render HTTP errors in the typed ErrorResponse shape."""
    body = ErrorResponse.model_validate({"error": {"code": exc.status_code, "message": exc.detail}})
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Render validation failures in the same typed shape as other errors."""
    body = ErrorResponse.model_validate({"error": {"code": 422, "message": str(exc.errors())}})
    return JSONResponse(status_code=422, content=body.model_dump())


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness probe used by Docker Compose and the deployment-validation agent."""
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics", tags=["ops"], include_in_schema=False)
def metrics() -> Response:
    """Prometheus exposition for this service."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _log_ingest(payload: AdEventCreate, outcome: str) -> None:
    """One structured line per delivery report, whatever became of it."""
    log_context(
        logger,
        "event ingested",
        service=settings.service_name,
        endpoint="/events",
        event_id=str(payload.event_id),
        event_type=payload.event_type,
        campaign_id=str(payload.campaign_id),
        member_id=payload.member_id,
        price_micros=payload.price_micros,
        status=outcome,
    )


@app.post(
    "/events",
    response_model=EventAck,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def ingest_event(
    payload: AdEventCreate,
    response: Response,
    session: Session = Depends(get_session),
) -> EventAck:
    """Record one impression or click. Replaying an event id is a no-op, not a double count.

    Deduplication is the primary key, not a read-then-write check: two concurrent
    reports of the same impression cannot both survive the insert.
    """
    session.add(AdEvent(**payload.model_dump()))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        DUPLICATES.labels(settings.service_name).inc()
        response.status_code = status.HTTP_200_OK
        _log_ingest(payload, "duplicate")
        return EventAck(event_id=payload.event_id, status="duplicate")

    EVENTS.labels(settings.service_name, payload.event_type).inc()
    if payload.event_type == "impression":
        SPEND.labels(settings.service_name).inc(payload.price_micros)
    _log_ingest(payload, "recorded")
    return EventAck(event_id=payload.event_id, status="recorded")


@app.get("/events", response_model=list[AdEventRead], responses=ERROR_RESPONSES)
def list_events(
    session: Session = Depends(get_session),
    campaign_id: uuid.UUID | None = Query(default=None),
    member_id: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[AdEvent]:
    """The most recent delivery reports, newest first — the raw tape behind the rollups."""
    query = select(AdEvent).order_by(AdEvent.occurred_at.desc()).limit(limit)
    if campaign_id is not None:
        query = query.where(AdEvent.campaign_id == campaign_id)
    if member_id is not None:
        query = query.where(AdEvent.member_id == member_id)
    return list(session.scalars(query).all())


@app.get("/campaigns/{campaign_id}/delivery", response_model=CampaignDelivery)
def campaign_delivery(
    campaign_id: uuid.UUID,
    session: Session = Depends(get_session),
    day: date | None = Query(default=None),
) -> CampaignDelivery:
    """What one campaign delivered — all zeroes if it has served nothing yet."""
    return delivery_for_campaign(session, campaign_id, day)


@app.get("/delivery", response_model=list[CampaignDelivery])
def delivery(
    session: Session = Depends(get_session),
    day: date | None = Query(default=None),
) -> list[CampaignDelivery]:
    """One delivery row per campaign that has served at least one event."""
    return delivery_rollup(session, day)
