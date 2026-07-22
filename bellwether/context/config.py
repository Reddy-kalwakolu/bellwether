"""Runtime configuration for the context layer."""

import os
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


def load_env_file(path: Path | None = None) -> list[str]:
    """Load `KEY=value` lines from `.env` into the environment; return the keys set.

    Read as `utf-8-sig`, not `utf-8`. PowerShell's `-Encoding utf8` writes a BOM, so
    the first line of a Windows-authored `.env` arrives as `\\ufeffVOYAGE_API_KEY` and
    silently never matches — which presents as "no API key" while the file plainly
    contains one. Existing environment variables always win.
    """
    target = path or (REPO_ROOT / ".env")
    if not target.exists():
        return []

    loaded: list[str] = []
    for line in target.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key and not os.environ.get(key):
            os.environ[key] = value.strip()
            loaded.append(key)
    return loaded
