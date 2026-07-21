"""ad-decision-service HTTP API.

The serving path. One ad request in, one decision out: eligibility, targeting, brand
safety, frequency capping, and budget pacing, in that order, over the active campaign
set read from campaign-service.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import redis as redis_lib
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from substrate.ad_decision_service.campaign_client import (
    CampaignClient,
    CampaignServiceError,
    build_client,
)
from substrate.ad_decision_service.config import settings
from substrate.ad_decision_service.decisioning import select
from substrate.ad_decision_service.schemas import (
    AdDecision,
    AdRequest,
    CandidateTrace,
    ErrorResponse,
    SelectedAd,
)
from substrate.ad_decision_service.store import DecisionStore, RedisDecisionStore
from substrate.shared.logging import configure_logging, log_context
from substrate.shared.observability import install_request_observability

logger = logging.getLogger("ad_decision_service.api")

DECISIONS = Counter(
    "ad_decisions_total",
    "Ad decisions by outcome.",
    ["service", "outcome"],
)
FILTERED = Counter(
    "ad_candidates_filtered_total",
    "Candidate campaigns by the rule that decided them.",
    ["service", "reason"],
)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorResponse, "description": "campaign-service unavailable"}
}

# Redis connections are established lazily, so importing this module — as the test
# suite does — never reaches for infrastructure.
_store: DecisionStore = RedisDecisionStore(
    redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
)


def get_store() -> DecisionStore:
    """FastAPI dependency returning the decision store. Overridden in tests."""
    return _store


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure logging before serving traffic."""
    configure_logging(settings.service_name)
    logger.info("ad-decision-service ready")
    yield


app = FastAPI(
    title="ad-decision-service",
    version="0.1.0",
    summary="Targeting, brand safety, frequency capping, and budget pacing on the serving path.",
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


def _no_fill_reason(trace: list[CandidateTrace]) -> str:
    """Summarize a no-fill as the most operationally interesting rule that fired.

    A no-fill has as many reasons as it had candidates. Reporting the near-miss —
    a campaign throttled by pacing — is more useful than reporting the campaign that
    was never in the running because it targets another country.
    """
    if not trace:
        return "no_candidates"
    order = (
        "pacing_throttled",
        "frequency_capped",
        "no_creative",
        "brand_safety_excluded",
        "targeting_mismatch",
        "outside_flight_window",
        "not_active",
    )
    reasons = {entry.reason for entry in trace}
    return next((reason for reason in order if reason in reasons), "no_candidates")


@app.post("/ad-request", response_model=AdDecision, responses=ERROR_RESPONSES)
def decide(
    ad_request: AdRequest,
    client: CampaignClient = Depends(build_client),
    store: DecisionStore = Depends(get_store),
) -> AdDecision:
    """Fill one ad slot, or explain in the response why nothing could fill it."""
    started = time.perf_counter()
    now = datetime.now(UTC)

    try:
        candidates = client.fetch_active_campaigns()
    except CampaignServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"campaign-service unavailable: {exc}",
        ) from exc

    outcome = select(
        candidates,
        ad_request,
        store,
        now=now,
        price_micros=settings.impression_price_micros,
        pacing_enabled=settings.pacing_enabled,
    )

    trace = [
        CandidateTrace(
            campaign_id=uuid.UUID(candidate.id), campaign_name=candidate.name, reason=reason
        )
        for candidate, reason in outcome.trace
    ]
    for _, reason in outcome.trace:
        FILTERED.labels(settings.service_name, reason).inc()

    ad: SelectedAd | None = None
    if outcome.winner is not None and outcome.creative is not None:
        store.record_impression(
            ad_request.member.member_id,
            outcome.winner.id,
            now.date(),
            settings.impression_price_micros,
        )
        ad = SelectedAd(
            campaign_id=uuid.UUID(outcome.winner.id),
            campaign_name=outcome.winner.name,
            advertiser=outcome.winner.advertiser,
            creative_id=uuid.UUID(outcome.creative.id),
            creative_name=outcome.creative.name,
            asset_url=outcome.creative.asset_url,
            duration_seconds=outcome.creative.duration_seconds,
            price_micros=settings.impression_price_micros,
        )

    DECISIONS.labels(settings.service_name, "filled" if ad else "no_fill").inc()
    decision = AdDecision(
        request_id=uuid.uuid4(),
        slot_id=ad_request.slot.slot_id,
        filled=ad is not None,
        ad=ad,
        no_fill_reason=None if ad else _no_fill_reason(trace),
        candidates_considered=len(candidates),
        trace=trace,
        decision_latency_ms=round((time.perf_counter() - started) * 1000, 3),
    )

    log_context(
        logger,
        "ad decision",
        service=settings.service_name,
        endpoint="/ad-request",
        latency_ms=decision.decision_latency_ms,
        member_id=ad_request.member.member_id,
        slot_id=decision.slot_id,
        filled=decision.filled,
        campaign_id=str(ad.campaign_id) if ad else None,
        no_fill_reason=decision.no_fill_reason,
        candidates_considered=decision.candidates_considered,
    )
    return decision
