"""Persistence behaviour of the campaign and creative models."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from substrate.campaign_service.models import Campaign, Creative


def _campaign() -> Campaign:
    starts_at = datetime.now(UTC)
    return Campaign(
        name="Stranger Things S5 Launch",
        advertiser="Acme Snacks",
        status="active",
        budget_micros=500_000_000,
        daily_budget_micros=50_000_000,
        frequency_cap_per_day=3,
        targeting={"countries": ["US"], "device_types": ["tv"], "content_ratings": ["TV-14"]},
        brand_safety_exclusions=["news"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=30),
    )


def test_campaign_round_trips_targeting_and_exclusions(session: Session) -> None:
    campaign = _campaign()
    session.add(campaign)
    session.commit()

    stored = session.scalars(select(Campaign)).one()

    assert stored.targeting["countries"] == ["US"]
    assert stored.brand_safety_exclusions == ["news"]
    assert stored.id is not None


def test_deleting_campaign_cascades_to_creatives(session: Session) -> None:
    campaign = _campaign()
    campaign.creatives.append(
        Creative(name="30s hero spot", duration_seconds=30, asset_url="https://cdn/x.mp4")
    )
    session.add(campaign)
    session.commit()

    session.delete(campaign)
    session.commit()

    assert session.scalars(select(Creative)).all() == []
