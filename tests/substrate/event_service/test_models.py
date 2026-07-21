"""The event table is append-only and keyed on the caller's event id."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from substrate.event_service.models import AdEvent
from substrate.event_service.schemas import AdEventCreate


def build(payload: dict[str, Any]) -> AdEvent:
    """Turn a validated create payload into a row."""
    return AdEvent(**AdEventCreate.model_validate(payload).model_dump())


def test_an_impression_round_trips(session: Session, impression_payload: dict[str, Any]) -> None:
    session.add(build(impression_payload))
    session.commit()

    stored = session.get(AdEvent, UUID(impression_payload["event_id"]))
    assert stored is not None
    assert stored.event_type == "impression"
    assert stored.price_micros == 2_000
    assert stored.member_id == "member-1"


def test_the_same_event_id_cannot_be_stored_twice(
    session: Session, impression_payload: dict[str, Any]
) -> None:
    session.add(build(impression_payload))
    session.commit()
    session.add(build(impression_payload))
    with pytest.raises(IntegrityError):
        session.commit()


def test_occurred_at_defaults_to_now_when_the_caller_omits_it(
    impression_payload: dict[str, Any],
) -> None:
    del impression_payload["occurred_at"]
    event = AdEventCreate.model_validate(impression_payload)
    assert (datetime.now(UTC) - event.occurred_at).total_seconds() < 5


def test_an_unknown_event_type_is_rejected(impression_payload: dict[str, Any]) -> None:
    impression_payload["event_type"] = "purchase"
    with pytest.raises(ValueError):
        AdEventCreate.model_validate(impression_payload)


def test_a_click_carries_no_spend(impression_payload: dict[str, Any]) -> None:
    impression_payload["event_type"] = "click"
    impression_payload["event_id"] = str(uuid4())
    del impression_payload["price_micros"]
    event = AdEventCreate.model_validate(impression_payload)
    assert event.price_micros == 0
