"""traffic-simulator control plane.

Load generation with a switch on it. The background loop drives real ad requests
through the substrate; the API decides how fast, how broken, and which failure is
currently injected — which is what makes the Level 3 demo loop possible: inject a
failure here, watch it appear in Grafana, hand it to an ops agent.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel, Field

from substrate.shared.logging import configure_logging, log_context
from substrate.shared.observability import install_request_observability
from substrate.traffic_simulator.clients import SubstrateClients, build_clients
from substrate.traffic_simulator.config import settings
from substrate.traffic_simulator.driver import tick
from substrate.traffic_simulator.population import Population
from substrate.traffic_simulator.scenarios import SCENARIOS, Scenario
from substrate.traffic_simulator.seeding import apply_mutation, seed_if_empty

logger = logging.getLogger("traffic_simulator.control")

AD_REQUESTS = Counter(
    "sim_ad_requests_total",
    "Ad requests the simulator issued, by outcome.",
    ["service", "outcome"],
)
EVENTS_REPORTED = Counter(
    "sim_events_reported_total",
    "Delivery events the simulator reported, by type.",
    ["service", "event_type"],
)
SCENARIO_INFO = Gauge(
    "sim_scenario_info",
    "1 for the scenario currently injected, 0 for every other.",
    ["service", "scenario"],
)


class ErrorDetail(BaseModel):
    """The body of a failed request."""

    code: int
    message: str


class ErrorResponse(BaseModel):
    """Every non-2xx response uses this shape."""

    error: ErrorDetail


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Unknown scenario"},
}


class ScenarioRead(BaseModel):
    """One injectable failure mode, as the API describes it."""

    name: str
    summary: str
    rate_multiplier: float
    malformed_fraction: float
    config_mutation: str | None


class ScenarioSelect(BaseModel):
    """Which failure mode to inject."""

    name: str = Field(min_length=1, max_length=64)


class ControlRequest(BaseModel):
    """Start or stop generating traffic."""

    running: bool


class SimulatorStatus(BaseModel):
    """What the simulator is doing right now, and what it has done so far."""

    running: bool
    scenario: str
    requests_per_second: float
    ticks: int
    fills: int
    no_fills: int
    rejected: int
    events_reported: int
    campaigns_changed: int = 0


class SimulatorState:
    """Mutable simulation state. One per process, replaced wholesale in tests."""

    def __init__(self) -> None:
        self.running = settings.autostart
        self.scenario: Scenario = SCENARIOS["steady"]
        self.population = Population(settings.seed)
        self.random = random.Random(settings.seed)
        self.ticks = 0
        self.fills = 0
        self.no_fills = 0
        self.rejected = 0
        self.events_reported = 0
        self.campaigns_changed = 0

    def snapshot(self) -> SimulatorStatus:
        """The current state as an API response."""
        return SimulatorStatus(
            running=self.running,
            scenario=self.scenario.name,
            requests_per_second=settings.requests_per_second * self.scenario.rate_multiplier,
            ticks=self.ticks,
            fills=self.fills,
            no_fills=self.no_fills,
            rejected=self.rejected,
            events_reported=self.events_reported,
            campaigns_changed=self.campaigns_changed,
        )


_state = SimulatorState()


def get_state() -> SimulatorState:
    """FastAPI dependency returning simulation state. Overridden in tests."""
    return _state


def _publish_scenario(active: str) -> None:
    """Set the active-scenario gauge to 1 and every other to 0."""
    for name in SCENARIOS:
        SCENARIO_INFO.labels(settings.service_name, name).set(1 if name == active else 0)


def _run_one_tick(state: SimulatorState, clients: SubstrateClients) -> None:
    """Drive one ad request and fold the result into state and metrics."""
    result = tick(
        clients,
        state.population,
        state.scenario,
        random_source=state.random,
        click_probability=settings.click_probability,
    )
    state.ticks += 1
    state.events_reported += result.events_reported

    if result.status_code != 200:
        state.rejected += 1
        AD_REQUESTS.labels(settings.service_name, "rejected").inc()
    elif result.filled:
        state.fills += 1
        AD_REQUESTS.labels(settings.service_name, "filled").inc()
        EVENTS_REPORTED.labels(settings.service_name, "impression").inc()
        if result.events_reported > 1:
            EVENTS_REPORTED.labels(settings.service_name, "click").inc()
    else:
        state.no_fills += 1
        AD_REQUESTS.labels(settings.service_name, "no_fill").inc()


async def _traffic_loop() -> None:
    """Generate traffic forever at the active scenario's rate."""
    clients = build_clients()
    with suppress(Exception):
        seeded = await asyncio.to_thread(seed_if_empty, clients)
        if seeded:
            log_context(logger, "seeded campaigns", service=settings.service_name, created=seeded)

    while True:
        state = get_state()
        rate = max(settings.requests_per_second * state.scenario.rate_multiplier, 0.1)
        await asyncio.sleep(1.0 / rate)
        if not state.running:
            continue
        # A substrate that is down is a condition to keep driving through, not to
        # die on — the whole point is to still be generating load during an incident.
        with suppress(Exception):
            await asyncio.to_thread(_run_one_tick, state, clients)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure logging, publish the starting scenario, and start the traffic loop."""
    configure_logging(settings.service_name)
    _publish_scenario("steady")
    task = asyncio.create_task(_traffic_loop())
    logger.info("traffic-simulator ready")
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(
    title="traffic-simulator",
    version="0.1.0",
    summary="Seeded ad-request load with five injectable failure modes.",
    lifespan=lifespan,
)


install_request_observability(app, settings.service_name, logger)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    """Render HTTP errors in the typed ErrorResponse shape."""
    body = ErrorResponse.model_validate({"error": {"code": exc.status_code, "message": exc.detail}})
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Render validation failures in the same typed shape as other errors."""
    body = ErrorResponse.model_validate({"error": {"code": 422, "message": str(exc.errors())}})
    return JSONResponse(status_code=422, content=body.model_dump())


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness probe used by Docker Compose and the deployment-validation agent."""
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics", tags=["ops"], include_in_schema=False)
def metrics() -> Response:
    """Prometheus exposition for this service."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/scenarios", response_model=list[ScenarioRead])
def list_scenarios() -> list[ScenarioRead]:
    """Every failure mode this simulator can inject, and what each one does."""
    return [ScenarioRead.model_validate(vars(scenario)) for scenario in SCENARIOS.values()]


@app.get("/status", response_model=SimulatorStatus)
def read_status(state: SimulatorState = Depends(get_state)) -> SimulatorStatus:
    """What the simulator is doing right now."""
    return state.snapshot()


@app.post("/scenario", response_model=SimulatorStatus, responses=ERROR_RESPONSES)
def select_scenario(
    payload: ScenarioSelect,
    state: SimulatorState = Depends(get_state),
    clients: SubstrateClients = Depends(build_clients),
) -> SimulatorStatus:
    """Inject a failure mode, applying any configuration change it calls for."""
    scenario = SCENARIOS.get(payload.name)
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown scenario {payload.name!r}; see GET /scenarios",
        )

    changed = apply_mutation(clients, scenario.config_mutation) if scenario.config_mutation else 0
    state.scenario = scenario
    state.campaigns_changed = changed
    _publish_scenario(scenario.name)

    log_context(
        logger,
        "scenario injected",
        service=settings.service_name,
        endpoint="/scenario",
        scenario=scenario.name,
        config_mutation=scenario.config_mutation,
        campaigns_changed=changed,
    )
    return state.snapshot()


@app.post("/control", response_model=SimulatorStatus)
def control(payload: ControlRequest, state: SimulatorState = Depends(get_state)) -> SimulatorStatus:
    """Start or stop generating traffic without restarting the container."""
    state.running = payload.running
    log_context(
        logger,
        "traffic toggled",
        service=settings.service_name,
        endpoint="/control",
        running=state.running,
    )
    return state.snapshot()


@app.post("/seed")
def seed(clients: SubstrateClients = Depends(build_clients)) -> dict[str, int]:
    """Create the seed campaign set, unless the platform already has campaigns."""
    return {"created": seed_if_empty(clients)}
