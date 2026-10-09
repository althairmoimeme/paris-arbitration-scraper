"""Project-wide configuration (env-driven)."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "paw.db"


class Settings(BaseSettings):
    """Loaded from .env (gitignored) with sensible defaults."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    paw_base_url: str = "https://parisarbitrationweek.com"
    paw_rate_limit_sleep: float = Field(default=0.5, ge=0.1, le=5.0)
    paw_user_agent: str = (
        "paris-arbitration-scraper/0.1 "
        "(BD research - contact a.bertantoine@gmail.com)"
    )
    anthropic_api_key: str = ""
    pappers_api_key: str = ""


settings = Settings()

# Make sure data dirs exist on import
RAW_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
