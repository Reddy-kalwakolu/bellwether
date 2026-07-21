"""Structured JSON logging shared by every substrate service.

One line of JSON per event keeps logs machine-readable, which is what lets the
Level 3 ops agents correlate them with metrics without brittle regex parsing.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON tagged with the emitting service."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        """Serialize `record`, merging any structured context into the payload."""
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload.update(context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service: str, level: int = logging.INFO) -> None:
    """Route the root logger through the JSON formatter for `service`."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def log_context(logger: logging.Logger, message: str, **context: Any) -> None:
    """Emit an INFO record carrying `context` as structured fields."""
    logger.info(message, extra={"context": context})
