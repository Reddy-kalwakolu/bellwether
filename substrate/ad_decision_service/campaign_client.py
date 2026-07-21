"""HTTP client for campaign-service.

The decision path reads campaigns through the public API, not the database. That is
deliberate: it keeps campaign-service the only writer of its schema, which is the
condition ADR-0002 named for deferring Alembic.
"""

from __future__ import annotations

import httpx

from substrate.ad_decision_service.config import settings
from substrate.ad_decision_service.decisioning import Candidate


class CampaignServiceError(Exception):
    """campaign-service was unreachable, or answered with an error status."""


class CampaignClient:
    """Reads the active campaign set that the decision path filters."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout_seconds, transport=transport)

    def fetch_active_campaigns(self) -> list[Candidate]:
        """Fetch every campaign currently in flight, as decision candidates."""
        try:
            response = self._client.get("/campaigns", params={"status": "active"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CampaignServiceError(str(exc)) from exc
        return [Candidate.from_api(payload) for payload in response.json()]

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._client.close()


_client = CampaignClient(settings.campaign_service_url, settings.request_timeout_seconds)


def build_client() -> CampaignClient:
    """FastAPI dependency returning the process-wide client. Overridden in tests."""
    return _client
