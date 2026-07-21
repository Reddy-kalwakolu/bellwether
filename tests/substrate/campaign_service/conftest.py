"""Fixtures backing campaign-service tests with hermetic in-memory SQLite."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from substrate.campaign_service.db import get_session
from substrate.campaign_service.main import app
from substrate.campaign_service.models import Base


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
def client(session: Session) -> Iterator[TestClient]:
    """A TestClient whose requests run against the in-memory session."""

    def override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def campaign_payload() -> dict[str, Any]:
    """A valid campaign creation payload with a live flight window."""
    starts_at = datetime.now(UTC)
    return {
        "name": "Stranger Things S5 Launch",
        "advertiser": "Acme Snacks",
        "status": "active",
        "budget_micros": 500_000_000,
        "daily_budget_micros": 50_000_000,
        "frequency_cap_per_day": 3,
        "targeting": {
            "countries": ["US", "CA"],
            "device_types": ["tv", "mobile"],
            "content_ratings": ["TV-14", "TV-MA"],
        },
        "brand_safety_exclusions": ["news", "true-crime"],
        "starts_at": starts_at.isoformat(),
        "ends_at": (starts_at + timedelta(days=30)).isoformat(),
    }
