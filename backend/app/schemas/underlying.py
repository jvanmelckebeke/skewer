from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class UnderlyingCreate(BaseModel):
    symbol: str = Field(..., description="Ticker to start collecting option chains for (US listings only).")
    group_id: int | None = Field(None, description="Group to add it to; the default group when omitted.")


class UnderlyingUpdate(BaseModel):
    is_active: bool | None = None
    dividend_yield: float | None = Field(
        None, ge=0, le=1,
        description="Continuous dividend yield used by the greeks. Setting it marks the value as manual.",
    )


class UnderlyingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    name: str
    currency: str
    dividend_yield: float
    dividend_yield_is_manual: bool
    is_active: bool
    last_polled_at: datetime | None
    last_eod_snapshot_on: date | None
    created_at: datetime


class UnderlyingStatus(BaseModel):
    """Collection health for one underlying — the 'is this working?' view."""

    symbol: str
    is_active: bool
    live_contracts: int
    total_contracts: int
    last_polled_at: datetime | None
    last_slot: datetime | None
    last_eod_snapshot_on: date | None
    minutes_since_poll: float | None
    market_open: bool
