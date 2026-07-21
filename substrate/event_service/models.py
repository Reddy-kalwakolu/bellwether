"""The event table: every impression and click the platform served.

Append-only on purpose. The primary key is the *caller's* event id, which is what
makes ingestion idempotent — a retried delivery report collides with itself instead
of inflating the numbers (ADR-0004).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every event-service table."""


class AdEvent(Base):
    """One impression or click, tied back to the decision that produced it."""

    __tablename__ = "ad_events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(16), index=True)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    creative_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    member_id: Mapped[str] = mapped_column(String(64), index=True)
    slot_id: Mapped[str] = mapped_column(String(64))
    # Clicks carry no spend; only an impression costs the advertiser anything.
    price_micros: Mapped[int] = mapped_column(Integer, default=0)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
