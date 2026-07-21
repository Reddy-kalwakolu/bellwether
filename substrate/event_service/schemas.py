"""Request and response models for the event-service API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal["impression", "click"]


def _now() -> datetime:
    """Ingestion timestamp for callers that do not supply one."""
    return datetime.now(UTC)


class AdEventCreate(BaseModel):
    """A delivery report: one impression or click that actually happened.

    `event_id` is supplied by the caller and is the idempotency key. A simulator
    (or a real SDK) that retries a report sends the same id and is deduplicated.
    """

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: EventType
    request_id: uuid.UUID
    campaign_id: uuid.UUID
    creative_id: uuid.UUID
    member_id: str = Field(min_length=1, max_length=64)
    slot_id: str = Field(min_length=1, max_length=64)
    price_micros: int = Field(default=0, ge=0)
    occurred_at: datetime = Field(default_factory=_now)


class AdEventRead(BaseModel):
    """An event as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    event_id: uuid.UUID
    event_type: EventType
    request_id: uuid.UUID
    campaign_id: uuid.UUID
    creative_id: uuid.UUID
    member_id: str
    slot_id: str
    price_micros: int
    occurred_at: datetime
    recorded_at: datetime | None = None


class EventAck(BaseModel):
    """The answer to an ingest: stored, or already known."""

    event_id: uuid.UUID
    status: Literal["recorded", "duplicate"]


class CampaignDelivery(BaseModel):
    """What a campaign actually delivered, aggregated from its events."""

    campaign_id: uuid.UUID
    impressions: int
    clicks: int
    click_through_rate: float
    spend_micros: int


class ErrorDetail(BaseModel):
    """The body of a failed request."""

    code: int
    message: str


class ErrorResponse(BaseModel):
    """Every non-2xx response uses this shape."""

    error: ErrorDetail
