"""campaign-service HTTP API.

The system of record for advertisers, campaigns, budgets, targeting, brand-safety
exclusions, and creatives. ad-decision-service reads from here when selecting an ad.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import select
from sqlalchemy.orm import Session

from substrate.campaign_service.config import settings
from substrate.campaign_service.db import engine, get_session
from substrate.campaign_service.models import Base, Campaign, Creative
from substrate.campaign_service.schemas import (
    CampaignCreate,
    CampaignRead,
    CampaignStatus,
    CampaignUpdate,
    CreativeCreate,
    CreativeRead,
    ErrorResponse,
)
from substrate.shared.logging import configure_logging, log_context

logger = logging.getLogger("campaign_service.api")

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled.",
    ["service", "endpoint", "method", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency.",
    ["service", "endpoint"],
)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Campaign not found"}
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure logging and ensure the schema exists before serving traffic."""
    configure_logging(settings.service_name)
    Base.metadata.create_all(engine)
    logger.info("campaign-service ready")
    yield


app = FastAPI(
    title="campaign-service",
    version="0.1.0",
    summary="System of record for advertisers, campaigns, and creatives.",
    lifespan=lifespan,
)


@app.middleware("http")
async def observe_request(
    request: Request,
    call_next: Callable[[Request], Coroutine[Any, Any, Response]],
) -> Response:
    """Record latency and outcome for every request, in metrics and in logs."""
    route: Any = request.scope.get("route")
    endpoint: str = route.path if route is not None else request.url.path
    started = time.perf_counter()
    response = await call_next(request)
    latency_s = time.perf_counter() - started

    REQUESTS.labels(settings.service_name, endpoint, request.method, response.status_code).inc()
    LATENCY.labels(settings.service_name, endpoint).observe(latency_s)
    log_context(
        logger,
        "request handled",
        service=settings.service_name,
        endpoint=endpoint,
        method=request.method,
        status=response.status_code,
        latency_ms=round(latency_s * 1000, 3),
    )
    return response


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


def _load_campaign(session: Session, campaign_id: uuid.UUID) -> Campaign:
    """Fetch a campaign or raise the 404 the API contract promises."""
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"campaign {campaign_id} not found"
        )
    return campaign


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness probe used by Docker Compose and the deployment-validation agent."""
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics", tags=["ops"], include_in_schema=False)
def metrics() -> Response:
    """Prometheus exposition for this service."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/campaigns", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
def create_campaign(payload: CampaignCreate, session: Session = Depends(get_session)) -> Campaign:
    """Open a new campaign."""
    campaign = Campaign(
        **payload.model_dump(exclude={"targeting"}),
        targeting=payload.targeting.model_dump(),
    )
    session.add(campaign)
    session.commit()
    return campaign


@app.get("/campaigns", response_model=list[CampaignRead])
def list_campaigns(
    session: Session = Depends(get_session),
    campaign_status: CampaignStatus | None = Query(default=None, alias="status"),
) -> list[Campaign]:
    """List campaigns, optionally filtered to a single status."""
    query = select(Campaign).order_by(Campaign.created_at)
    if campaign_status is not None:
        query = query.where(Campaign.status == campaign_status)
    return list(session.scalars(query).all())


@app.get("/campaigns/{campaign_id}", response_model=CampaignRead, responses=ERROR_RESPONSES)
def get_campaign(campaign_id: uuid.UUID, session: Session = Depends(get_session)) -> Campaign:
    """Fetch one campaign and its creatives."""
    return _load_campaign(session, campaign_id)


@app.patch("/campaigns/{campaign_id}", response_model=CampaignRead, responses=ERROR_RESPONSES)
def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    session: Session = Depends(get_session),
) -> Campaign:
    """Apply a partial update — pausing a flight, raising a budget, retargeting."""
    campaign = _load_campaign(session, campaign_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "targeting" in changes:
        changes["targeting"] = payload.targeting.model_dump() if payload.targeting else {}
    for field, value in changes.items():
        setattr(campaign, field, value)
    session.commit()
    return campaign


@app.delete(
    "/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT, responses=ERROR_RESPONSES
)
def delete_campaign(campaign_id: uuid.UUID, session: Session = Depends(get_session)) -> None:
    """Remove a campaign and every creative attached to it."""
    session.delete(_load_campaign(session, campaign_id))
    session.commit()


@app.post(
    "/campaigns/{campaign_id}/creatives",
    response_model=CreativeRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def add_creative(
    campaign_id: uuid.UUID,
    payload: CreativeCreate,
    session: Session = Depends(get_session),
) -> Creative:
    """Attach an ad asset to a campaign."""
    campaign = _load_campaign(session, campaign_id)
    creative = Creative(campaign_id=campaign.id, **payload.model_dump())
    session.add(creative)
    session.commit()
    return creative


@app.get(
    "/campaigns/{campaign_id}/creatives",
    response_model=list[CreativeRead],
    responses=ERROR_RESPONSES,
)
def list_creatives(
    campaign_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[Creative]:
    """List the creatives available to a campaign."""
    return list(_load_campaign(session, campaign_id).creatives)
