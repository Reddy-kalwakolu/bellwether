"""HTTP access to the three substrate services, behind one narrow protocol.

The driver never imports httpx. It takes this protocol, which is what lets the
whole simulation be exercised in tests without opening a socket.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from substrate.traffic_simulator.config import settings


class SubstrateClients(Protocol):
    """Everything the simulator needs from the rest of the substrate."""

    def list_campaigns(self) -> list[dict[str, Any]]:
        """Every campaign campaign-service knows about."""
        ...

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Open a campaign and return it as created."""
        ...

    def add_creative(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach an ad asset to a campaign."""
        ...

    def patch_campaign(self, campaign_id: str, payload: dict[str, Any]) -> None:
        """Apply a partial update — this is how a failure mode gets injected."""
        ...

    def ad_request(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Ask for an ad. Returns the status code with the body: a 422 is data, not an error."""
        ...

    def report_event(self, payload: dict[str, Any]) -> int:
        """Report an impression or click; returns the status code."""
        ...


class HttpSubstrateClients:
    """The real thing: three httpx clients, one per service."""

    def __init__(
        self,
        campaign_service_url: str,
        ad_decision_url: str,
        event_service_url: str,
        timeout_seconds: float,
    ) -> None:
        self._campaigns = httpx.Client(base_url=campaign_service_url, timeout=timeout_seconds)
        self._decisions = httpx.Client(base_url=ad_decision_url, timeout=timeout_seconds)
        self._events = httpx.Client(base_url=event_service_url, timeout=timeout_seconds)

    def list_campaigns(self) -> list[dict[str, Any]]:
        """Every campaign campaign-service knows about."""
        response = self._campaigns.get("/campaigns")
        response.raise_for_status()
        campaigns: list[dict[str, Any]] = response.json()
        return campaigns

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Open a campaign and return it as created."""
        response = self._campaigns.post("/campaigns", json=payload)
        response.raise_for_status()
        campaign: dict[str, Any] = response.json()
        return campaign

    def add_creative(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach an ad asset to a campaign."""
        response = self._campaigns.post(f"/campaigns/{campaign_id}/creatives", json=payload)
        response.raise_for_status()
        creative: dict[str, Any] = response.json()
        return creative

    def patch_campaign(self, campaign_id: str, payload: dict[str, Any]) -> None:
        """Apply a partial update — this is how a failure mode gets injected."""
        response = self._campaigns.patch(f"/campaigns/{campaign_id}", json=payload)
        response.raise_for_status()

    def ad_request(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Ask for an ad. Returns the status code with the body: a 422 is data, not an error."""
        response = self._decisions.post("/ad-request", json=payload)
        body: dict[str, Any] = response.json()
        return response.status_code, body

    def report_event(self, payload: dict[str, Any]) -> int:
        """Report an impression or click; returns the status code."""
        return self._events.post("/events", json=payload).status_code

    def close(self) -> None:
        """Release all three connection pools."""
        self._campaigns.close()
        self._decisions.close()
        self._events.close()


_clients = HttpSubstrateClients(
    settings.campaign_service_url,
    settings.ad_decision_url,
    settings.event_service_url,
    settings.request_timeout_seconds,
)


def build_clients() -> SubstrateClients:
    """FastAPI dependency returning the process-wide clients. Overridden in tests."""
    return _clients
