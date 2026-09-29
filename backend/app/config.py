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

    # --- IBKR (the shared ibkr-svc gateway) --------------------------------
    # Reached over the external docker network `ibkr`. Empty disables every
    # IBKR path: no conId matching, no IBKR provider.
    ibkr_svc_url: str = ""
    ibkr_timeout_seconds: float = 60.0

    # --- Euronext (EWS derivatives web service) ---------------------------
    # Requires a licensed auth key from Euronext London; there is no anonymous
    # mode. With the key unset the provider refuses to poll rather than
    # emitting 401s, and rejects an underlying at add time.
    euronext_api_url: str = "https://gateway.euronext.com/api/instrumentDetail"
    euronext_auth_key: str = ""
    # "RT" real-time or "DT" delayed, per spec section 3.1. Which one a key is
    # entitled to is a licensing question.
    euronext_session_quality: str = "DT"
    euronext_timeout_seconds: float = 20.0

    # This API serves one contract per request and has no chain endpoint, so a
    # chain is an enumerated grid and these bounds are what stand between a
    # poll and several thousand requests. The cap is a hard stop, not advice.
    euronext_concurrency: int = 4
    euronext_max_requests: int = 600
    # Strikes within +/- this fraction of spot. 0.30 covers the strikes with
    # meaningful open interest on a typical equity name.
    euronext_strike_window_pct: float = 0.30
    euronext_max_maturities: int = 6

    log_level: str = "INFO"

    model_config = {"env_prefix": ""}


settings = Settings()
