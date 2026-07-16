"""Application settings loaded from environment variables.

Only variable *names* live in the repository. Real values are supplied at runtime
through the environment (see ``.env.example``); no secret is ever committed.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the API service.

    The default ``database_url`` targets the local Docker Compose ``postgres``
    service with a non-secret local development credential. It must be overridden
    in any non-local environment.
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_name: str = "FreeTier Atlas API"
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://atlas:atlas@postgres:5432/atlas"  # noqa: E501 # pragma: allowlist secret

    # Seconds to wait for a readiness database probe before reporting not-ready.
    readiness_timeout_seconds: float = 3.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""

    return Settings()
