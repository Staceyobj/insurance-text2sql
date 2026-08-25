"""Application settings (SPEC §8), loaded from environment variables / .env.

ADMIN_DATABASE_URL is deliberately NOT part of these settings: the running
application must never hold the admin connection string (SPEC §4.3). The
seed flow reads it directly from the environment (db/seed.py).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every field maps to an environment variable of the same name."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env may hold ADMIN_DATABASE_URL for the seed flow
    )

    # --- LLM ---
    # Empty by default and only validated when an LLM is actually built, so
    # that `make test` stays fully offline (no key required to import config).
    zhipuai_api_key: str = ""
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"  # /v4, not /v1
    llm_model: str = "glm-4.7"
    llm_thinking_enabled: bool = False
    # Request timeout in seconds: turns a hung call into a retryable transport
    # error instead of an infinite wait (see llm.py TRANSPORT_ERRORS).
    llm_timeout_s: float = 300.0

    # --- Database (application holds the read-only role only) ---
    database_url: str = "postgresql://t2s_readonly:t2s_readonly@localhost:5432/insurance"

    # --- Pipeline ---
    row_limit: int = 200
    max_retries: int = 2

    # --- Misc ---
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings (override in tests via Settings(...))."""
    return Settings()
