from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://skewer:skewer@db:5432/skewer"

    # The intraday poll grid. Every cadence bucket in app/domain/cadence.py is
    # a multiple of this, so changing it rescales the whole schedule.
    base_poll_minutes: int = 5

    # End-of-day full-chain snapshot (the one poll that persists every
    # contract regardless of cadence bucket, including LEAPS).
    eod_snapshot_cron: str = "10 21 * * 1-5"

    # Annualised risk-free rate for Black-Scholes, ACT/365. A constant is a
    # deliberate simplification: at typical equity-option tenors the greeks are
    # far more sensitive to IV than to a few bp of rate. Swap for a term
    # structure (^IRX/^FVX) when that stops being true.
    risk_free_rate: float = 0.04

    # The provider used for underlyings that do not say otherwise. Kept for
    # backwards compatibility and as the default stamped on a newly added
    # underlying; it is no longer a global switch, because skewer now polls
    # Yahoo and Eurex in the same cycle. Routing lives on
    # ``underlyings.venue`` — see services/option_providers/__init__.py.
    option_provider: str = "yahoo"

    # --- Eurex (Deutsche Boerse public reference-data API) ----------------
    # The published shared key is anonymous and rate limited. It is fine for a
    # handful of European names on a five-minute grid; register a dedicated
    # key at console.developer.deutsche-boerse.com if that stops being true.
    # Note the trailing /graphql: Eurex's own documentation gives the URL
    # without it, and that path 404s.
    eurex_graphql_url: str = (
        "https://api.developer.deutsche-boerse.com/eurex-prod-graphql/graphql"
    )
    eurex_api_key: str = "68cdafd2-c5c1-49be-8558-37244ab4f513"
    eurex_timeout_seconds: float = 30.0

    log_level: str = "INFO"

    model_config = {"env_prefix": ""}


settings = Settings()
