"""Runtime configuration for campaign-service."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings read from `CAMPAIGN_*` environment variables.

    The default points at the host-published Postgres port so the service can be
    run outside Docker; inside the compose network the URL is supplied explicitly.
    """

    model_config = SettingsConfigDict(env_prefix="CAMPAIGN_")

    database_url: str = "postgresql+psycopg://bellwether:bellwether@localhost:5433/bellwether"
    service_name: str = "campaign-service"


settings = Settings()
