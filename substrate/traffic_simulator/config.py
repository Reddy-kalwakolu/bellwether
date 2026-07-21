"""Runtime configuration for traffic-simulator."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings read from `SIM_*` environment variables.

    Defaults point at host-published ports so the simulator runs outside Docker;
    inside the compose network the URLs are supplied explicitly.
    """

    model_config = SettingsConfigDict(env_prefix="SIM_")

    service_name: str = "traffic-simulator"
    campaign_service_url: str = "http://localhost:8001"
    ad_decision_url: str = "http://localhost:8002"
    event_service_url: str = "http://localhost:8003"
    # Sized against the seed campaigns' daily budgets: much above this and even
    # pacing works correctly by throttling everything, which makes a healthy
    # baseline indistinguishable from an injected failure.
    requests_per_second: float = 2.0
    # Roughly an order of magnitude above real CTV click rates, so a demo shows
    # clicks within a minute instead of within an hour.
    click_probability: float = 0.08
    seed: int = 20260721
    request_timeout_seconds: float = 2.0
    autostart: bool = True


settings = Settings()
