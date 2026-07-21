"""Request metrics and the request log line, shared by every substrate service.

The two HTTP metrics are defined once, here, rather than once per service. Prometheus
keeps a single global registry per process, so duplicate definitions collide the moment
two services are imported together — as they are in the test suite. The `service` label
is what separates them, not the metric name.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Histogram

from substrate.shared.logging import log_context

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled.",
    ["service", "endpoint", "method", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency.",
    ["service", "endpoint"],
)


def install_request_observability(app: FastAPI, service_name: str, logger: logging.Logger) -> None:
    """Record latency and outcome for every request, in metrics and in logs."""

    @app.middleware("http")
    async def observe_request(
        request: Request,
        call_next: Callable[[Request], Coroutine[Any, Any, Response]],
    ) -> Response:
        """Time one request, then count it and log it."""
        route: Any = request.scope.get("route")
        endpoint: str = route.path if route is not None else request.url.path
        started = time.perf_counter()
        response = await call_next(request)
        latency_s = time.perf_counter() - started

        REQUESTS.labels(service_name, endpoint, request.method, response.status_code).inc()
        LATENCY.labels(service_name, endpoint).observe(latency_s)
        log_context(
            logger,
            "request handled",
            service=service_name,
            endpoint=endpoint,
            method=request.method,
            status=response.status_code,
            latency_ms=round(latency_s * 1000, 3),
        )
        return response
