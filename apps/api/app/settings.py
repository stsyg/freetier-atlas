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

    # Path to the declarative ``llm-providers`` YAML (env ``LLM_CONFIG_PATH``).
    # ``None`` (the default) means no LLM layer is configured: the adviser runs
    # deterministic-only. Loading is fail-safe -- a missing/invalid file degrades
    # to deterministic-only rather than crashing the API (see
    # ``app.adviser.llm.runtime.get_llm_section``).
    llm_config_path: str | None = None

    # ---- Public-adviser abuse controls (F007 slice 2) ------------------- #
    # Master switch for the abuse layer (per-IP rate limiting, dedupe, circuit
    # breaker, proof-of-work). When off, every enforcement point fails open --
    # useful for local debugging only; production keeps it on.
    abuse_enabled: bool = True

    # Server secret used ONLY to (a) HMAC-hash client IPs before they are stored
    # (a raw IP is NEVER persisted) and (b) sign self-hosted proof-of-work
    # challenge tokens. This is a NON-SECRET local development default and MUST be
    # overridden with a real random value (env ``ABUSE_SECRET``) in any deployed
    # environment.
    abuse_secret: str = "local-dev-abuse-secret-not-for-production"  # pragma: allowlist secret

    # Fixed-window size for per-IP rate limiting, in seconds. Defaults to one day
    # so it matches the ``*_requests_per_ip_per_day`` limits from the LLM config.
    abuse_rate_window_seconds: int = 86_400

    # Window (seconds) within which two byte-identical request bodies from the
    # same IP are collapsed as a duplicate (dedupe), so a burst does not multiply
    # LLM calls or burn the rate limit.
    abuse_dedupe_window_seconds: int = 10

    # Circuit breaker: after this many consecutive provider failures/timeouts the
    # breaker opens for ``abuse_breaker_cooldown_seconds``; a single half-open
    # probe is allowed after the cooldown.
    abuse_breaker_threshold: int = 3
    abuse_breaker_cooldown_seconds: int = 30

    # Self-hosted proof-of-work difficulty (number of leading hex zeros required
    # in the SHA-256 of ``token:nonce``) and challenge time-to-live (seconds).
    # The difficulty is deliberately low so tests and honest clients solve it
    # quickly; it exists to make automated abuse of the expensive AI path costly.
    abuse_pow_difficulty: int = 1
    abuse_pow_ttl_seconds: int = 300

    # Optional environment override that forces the AI kill switch ON regardless
    # of the persisted ``abuse_flag`` row (the effective switch is DB OR this).
    # The persisted switch is the admin-togglable source of truth (F007 slice 4).
    ai_kill_switch: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""

    return Settings()
