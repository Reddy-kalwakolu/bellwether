"""Runtime configuration for the context layer."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# bellwether/context/config.py -> bellwether/context -> bellwether -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Settings read from `CONTEXT_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="CONTEXT_")

    repo_root: Path = REPO_ROOT
    # `data/` is gitignored and excluded from discovery, so the pipeline can
    # never ingest its own output.
    corpus_path: Path = Path("data/context/corpus.jsonl")


settings = Settings()
