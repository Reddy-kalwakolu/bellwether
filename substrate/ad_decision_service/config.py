"""Runtime configuration for ad-decision-service."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings read from `AD_DECISION_*` environment variables.

    Defaults point at the host-published ports so the service can be run outside
    Docker; inside the compose network the URLs are supplied explicitly.
    """

    model_config = SettingsConfigDict(env_prefix="AD_DECISION_")

    service_name: str = "ad-decision-service"
    campaign_service_url: str = "http://localhost:8001"
    redis_url: str = "redis://localhost:6380/0"
    request_timeout_seconds: float = 2.0
    # Flat price per impression. Real platforms clear an auction; the substrate only
    # needs spend to accumulate believably so pacing has something to pace.
    impression_price_micros: int = 2_000
    pacing_enabled: bool = True


settings = Settings()
