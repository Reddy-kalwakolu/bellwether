"""SQLAlchemy models for campaigns and their creatives."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for every campaign-service table."""


class Campaign(Base):
    """An advertiser's flight: what to serve, to whom, for how much, and when."""

    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    advertiser: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    budget_micros: Mapped[int] = mapped_column(Integer)
    daily_budget_micros: Mapped[int] = mapped_column(Integer)
    frequency_cap_per_day: Mapped[int] = mapped_column(Integer, default=3)
    targeting: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    brand_safety_exclusions: Mapped[list[str]] = mapped_column(JSON, default=list)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    creatives: Mapped[list[Creative]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", lazy="selectin"
    )


class Creative(Base):
    """A single ad asset belonging to a campaign."""

    __tablename__ = "creatives"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    asset_url: Mapped[str] = mapped_column(String(500))

    campaign: Mapped[Campaign] = relationship(back_populates="creatives")
