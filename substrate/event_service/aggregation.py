"""Delivery rollups, computed on read.

One `GROUP BY campaign_id` over the event table answers every delivery question
the substrate currently asks. There is no materialized rollup table: at Level 0
traffic volumes the query is trivial, and a rollup would be a second copy of the
truth to keep in sync. ADR-0004 records the trigger that adds one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, case, func, select
from sqlalchemy.orm import Session

from substrate.event_service.models import AdEvent
from substrate.event_service.schemas import CampaignDelivery

IMPRESSION = "impression"
CLICK = "click"


def click_through_rate(impressions: int, clicks: int) -> float:
    """Clicks per impression, and zero rather than an error when nothing served."""
    if impressions <= 0:
        return 0.0
    return round(clicks / impressions, 6)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC half-open interval `[start, end)` covering `day`."""
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _counters() -> tuple[ColumnElement[Any], ColumnElement[Any], ColumnElement[Any]]:
    """The three aggregate expressions every delivery query selects."""
    impressions = func.sum(case((AdEvent.event_type == IMPRESSION, 1), else_=0))
    clicks = func.sum(case((AdEvent.event_type == CLICK, 1), else_=0))
    spend = func.sum(case((AdEvent.event_type == IMPRESSION, AdEvent.price_micros), else_=0))
    return impressions, clicks, spend


def _scoped(query: Select[Any], day: date | None) -> Select[Any]:
    """Narrow a delivery query to a single UTC day, when one was asked for."""
    if day is None:
        return query
    start, end = day_bounds(day)
    return query.where(AdEvent.occurred_at >= start, AdEvent.occurred_at < end)


def _delivery(
    campaign_id: uuid.UUID, impressions: int, clicks: int, spend: int
) -> CampaignDelivery:
    """Assemble one delivery row, deriving CTR from the counts."""
    return CampaignDelivery(
        campaign_id=campaign_id,
        impressions=impressions,
        clicks=clicks,
        click_through_rate=click_through_rate(impressions, clicks),
        spend_micros=spend,
    )


def delivery_for_campaign(
    session: Session, campaign_id: uuid.UUID, day: date | None
) -> CampaignDelivery:
    """What one campaign delivered — all zeroes if it has served nothing yet."""
    impressions, clicks, spend = _counters()
    query = _scoped(
        select(impressions, clicks, spend).where(AdEvent.campaign_id == campaign_id), day
    )
    row = session.execute(query).one()
    return _delivery(campaign_id, int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))


def delivery_rollup(session: Session, day: date | None) -> list[CampaignDelivery]:
    """One delivery row per campaign that has served at least one event."""
    impressions, clicks, spend = _counters()
    query = _scoped(select(AdEvent.campaign_id, impressions, clicks, spend), day)
    query = query.group_by(AdEvent.campaign_id).order_by(AdEvent.campaign_id)
    return [
        _delivery(row[0], int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
        for row in session.execute(query).all()
    ]
