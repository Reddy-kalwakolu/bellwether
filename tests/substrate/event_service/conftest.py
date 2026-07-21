"""Fixtures backing event-service tests with hermetic in-memory SQLite."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from substrate.event_service.db import get_session
from substrate.event_service.main import app
from substrate.event_service.models import Base

CAMPAIGN_ID = "11111111-1111-1111-1111-111111111111"
CREATIVE_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session() -> Iterator[Session]:
    """A session bound to a fresh in-memory database per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def impression_payload() -> dict[str, Any]:
    """One served impression, as the traffic-simulator will report it."""
    return {
        "event_id": str(uuid4()),
        "event_type": "impression",
        "request_id": str(uuid4()),
        "campaign_id": CAMPAIGN_ID,
        "creative_id": CREATIVE_ID,
        "member_id": "member-1",
        "slot_id": "slot-1",
        "price_micros": 2_000,
        "occurred_at": datetime.now(UTC).isoformat(),
    }


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    """A TestClient whose requests run against the in-memory session.

    Built without entering its context manager on purpose: the lifespan provisions
    the schema on the real Postgres engine, and this suite needs no infrastructure.
    """

    def override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()
