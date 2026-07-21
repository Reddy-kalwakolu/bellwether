"""Request and response models for the campaign-service API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CampaignStatus = Literal["draft", "active", "paused", "completed"]

# Budgets are stored in a 32-bit integer column, which caps a budget at ~$2,147.
# Stating that bound here makes it part of the API contract and a 422, rather than
# a Postgres `NumericValueOutOfRange` surfacing as a 500. Day 5's simulator found
# this by trying to inject a budget runaway. Raising the ceiling means migrating
# the column to BIGINT, which is precisely the trigger ADR-0002 is waiting on.
MAX_MICROS = 2_147_483_647


class Targeting(BaseModel):
    """Who a campaign is eligible to reach."""

    countries: list[str] = Field(default_factory=list)
    device_types: list[str] = Field(default_factory=list)
    content_ratings: list[str] = Field(default_factory=list)


class CampaignCreate(BaseModel):
    """Payload for opening a new campaign."""

    name: str = Field(min_length=1, max_length=120)
    advertiser: str = Field(min_length=1, max_length=120)
    status: CampaignStatus = "draft"
    budget_micros: int = Field(gt=0, le=MAX_MICROS)
    daily_budget_micros: int = Field(gt=0, le=MAX_MICROS)
    frequency_cap_per_day: int = Field(default=3, ge=1, le=50)
    targeting: Targeting = Field(default_factory=Targeting)
    brand_safety_exclusions: list[str] = Field(default_factory=list)
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def check_flight_window(self) -> CampaignCreate:
        """A flight must end after it starts, and pace within its total budget."""
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.daily_budget_micros > self.budget_micros:
            raise ValueError("daily_budget_micros cannot exceed budget_micros")
        return self


class CampaignUpdate(BaseModel):
    """Partial update; omitted fields are left untouched."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: CampaignStatus | None = None
    budget_micros: int | None = Field(default=None, gt=0, le=MAX_MICROS)
    daily_budget_micros: int | None = Field(default=None, gt=0, le=MAX_MICROS)
    frequency_cap_per_day: int | None = Field(default=None, ge=1, le=50)
    targeting: Targeting | None = None
    brand_safety_exclusions: list[str] | None = None


class CreativeCreate(BaseModel):
    """Payload for attaching an ad asset to a campaign."""

    name: str = Field(min_length=1, max_length=120)
    duration_seconds: int = Field(gt=0, le=180)
    asset_url: str = Field(min_length=1, max_length=500)


class CreativeRead(BaseModel):
    """A creative as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    name: str
    duration_seconds: int
    asset_url: str


class CampaignRead(BaseModel):
    """A campaign as returned by the API, including its creatives."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    advertiser: str
    status: CampaignStatus
    budget_micros: int
    daily_budget_micros: int
    frequency_cap_per_day: int
    targeting: dict[str, list[str]]
    brand_safety_exclusions: list[str]
    starts_at: datetime
    ends_at: datetime
    created_at: datetime
    creatives: list[CreativeRead] = Field(default_factory=list)


class ErrorDetail(BaseModel):
    """The body of a failed request."""

    code: int
    message: str


class ErrorResponse(BaseModel):
    """Every non-2xx response uses this shape."""

    error: ErrorDetail
