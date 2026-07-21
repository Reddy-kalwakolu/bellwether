"""Tests for the shared structured logging package."""

import json
import logging

from substrate.shared.logging import JsonFormatter, log_context


def _record(message: str = "ad request served", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="campaign_service.api",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_json_with_service_name() -> None:
    payload = json.loads(JsonFormatter("campaign-service").format(_record()))

    assert payload["service"] == "campaign-service"
    assert payload["level"] == "INFO"
    assert payload["message"] == "ad request served"
    assert "ts" in payload


def test_formatter_merges_context_fields() -> None:
    record = _record(context={"endpoint": "/campaigns", "latency_ms": 12.5})

    payload = json.loads(JsonFormatter("campaign-service").format(record))

    assert payload["endpoint"] == "/campaigns"
    assert payload["latency_ms"] == 12.5


def test_formatter_serializes_exceptions() -> None:
    try:
        raise ValueError("budget must be positive")
    except ValueError:
        import sys

        record = _record("campaign rejected")
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter("campaign-service").format(record))

    assert "budget must be positive" in payload["exception"]


def test_log_context_attaches_fields() -> None:
    logger = logging.getLogger("test.context")
    logger.setLevel(logging.INFO)
    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger.addHandler(Capture())
    log_context(logger, "campaign created", campaign_id="abc", latency_ms=3.0)

    assert captured[0].context == {"campaign_id": "abc", "latency_ms": 3.0}  # type: ignore[attr-defined]
