"""Delivery rollups: impressions, clicks, CTR, and spend, computed on read."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from substrate.event_service.aggregation import (
    click_through_rate,
    delivery_for_campaign,
    delivery_rollup,
)
from substrate.event_service.models import AdEvent

CAMPAIGN_A = UUID("11111111-1111-1111-1111-111111111111")
CAMPAIGN_B = UUID("33333333-3333-3333-3333-333333333333")
DAY = date(2026, 7, 21)
NOON = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def record(session: Session, **overrides: object) -> None:
    """Insert one event, defaulting every field the test does not care about."""
    fields: dict[str, Any] = {
        "event_id": uuid4(),
        "event_type": "impression",
        "request_id": uuid4(),
        "campaign_id": CAMPAIGN_A,
        "creative_id": uuid4(),
        "member_id": "member-1",
        "slot_id": "slot-1",
        "price_micros": 2_000,
        "occurred_at": NOON,
    }
    fields.update(overrides)
    session.add(AdEvent(**fields))
    session.commit()


def test_click_through_rate_is_zero_when_nothing_was_served() -> None:
    assert click_through_rate(0, 0) == 0.0
    assert click_through_rate(0, 5) == 0.0


def test_click_through_rate_is_clicks_over_impressions() -> None:
    assert click_through_rate(4, 1) == 0.25


def test_a_campaign_with_no_events_delivers_zeroes(session: Session) -> None:
    delivery = delivery_for_campaign(session, CAMPAIGN_A, day=None)
    assert delivery.impressions == 0
    assert delivery.clicks == 0
    assert delivery.spend_micros == 0
    assert delivery.click_through_rate == 0.0


def test_delivery_counts_impressions_clicks_and_impression_spend(session: Session) -> None:
    for _ in range(4):
        record(session)
    record(session, event_type="click", price_micros=0)

    delivery = delivery_for_campaign(session, CAMPAIGN_A, day=None)
    assert delivery.impressions == 4
    assert delivery.clicks == 1
    assert delivery.spend_micros == 8_000
    assert delivery.click_through_rate == 0.25


def test_delivery_is_scoped_to_one_campaign(session: Session) -> None:
    record(session)
    record(session, campaign_id=CAMPAIGN_B)
    record(session, campaign_id=CAMPAIGN_B)

    assert delivery_for_campaign(session, CAMPAIGN_A, day=None).impressions == 1
    assert delivery_for_campaign(session, CAMPAIGN_B, day=None).impressions == 2


def test_a_day_filter_excludes_yesterdays_events(session: Session) -> None:
    record(session)
    record(session, occurred_at=NOON - timedelta(days=1))

    assert delivery_for_campaign(session, CAMPAIGN_A, day=DAY).impressions == 1
    assert delivery_for_campaign(session, CAMPAIGN_A, day=None).impressions == 2


def test_the_rollup_returns_one_row_per_campaign_in_id_order(session: Session) -> None:
    record(session, campaign_id=CAMPAIGN_B)
    record(session)
    record(session, event_type="click", price_micros=0)

    rollup = delivery_rollup(session, day=None)
    assert [row.campaign_id for row in rollup] == [CAMPAIGN_A, CAMPAIGN_B]
    assert rollup[0].impressions == 1
    assert rollup[0].clicks == 1
    assert rollup[1].spend_micros == 2_000


def test_the_rollup_is_empty_before_anything_is_served(session: Session) -> None:
    assert delivery_rollup(session, day=None) == []
