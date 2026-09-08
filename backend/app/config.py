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

    option_provider: str = "yahoo"
    log_level: str = "INFO"

    model_config = {"env_prefix": ""}


settings = Settings()
