"""Request and response models for the ad-decision-service API."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FilterReason = Literal[
    "eligible",
    "not_active",
    "outside_flight_window",
    "targeting_mismatch",
    "brand_safety_excluded",
    "frequency_capped",
    "pacing_throttled",
    "no_creative",
]


class MemberContext(BaseModel):
    """Who is watching, as far as the decision path is concerned."""

    member_id: str = Field(min_length=1, max_length=64)
    country: str = Field(min_length=2, max_length=2)
    device_type: str = Field(min_length=1, max_length=32)


class Slot(BaseModel):
    """The ad break being filled, and the content surrounding it."""

    slot_id: str = Field(min_length=1, max_length=64)
    duration_seconds: int = Field(gt=0, le=180)
    content_rating: str = Field(min_length=1, max_length=16)
    content_categories: list[str] = Field(default_factory=list)


class AdRequest(BaseModel):
    """One opportunity to serve an ad."""

    member: MemberContext
    slot: Slot


class CandidateTrace(BaseModel):
    """Why one campaign won or lost. The decision path's audit trail."""

    campaign_id: uuid.UUID
    campaign_name: str
    reason: FilterReason


class SelectedAd(BaseModel):
    """The ad chosen for a slot."""

    campaign_id: uuid.UUID
    campaign_name: str
    advertiser: str
    creative_id: uuid.UUID
    creative_name: str
    asset_url: str
    duration_seconds: int
    price_micros: int


class AdDecision(BaseModel):
    """The response to an ad request: a fill, or an explained no-fill."""

    model_config = ConfigDict(from_attributes=True)

    request_id: uuid.UUID
    slot_id: str
    filled: bool
    ad: SelectedAd | None = None
    no_fill_reason: str | None = None
    candidates_considered: int
    trace: list[CandidateTrace] = Field(default_factory=list)
    decision_latency_ms: float


class ErrorDetail(BaseModel):
    """The body of a failed request."""

    code: int
    message: str


class ErrorResponse(BaseModel):
    """Every non-2xx response uses this shape."""

    error: ErrorDetail
