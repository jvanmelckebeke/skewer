from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.venue import Venue


class UnderlyingCreate(BaseModel):
    symbol: str = Field(..., description="Ticker to start collecting option chains for.")
    group_id: int | None = Field(None, description="Group to add it to; the default group when omitted.")
    venue: Venue | None = Field(
        None,
        description=(
            "Which market's options to collect. Omit to auto-detect: OCC (Yahoo, "
            "US listings) is tried first, then Eurex."
        ),
    )
    venue_product: str | None = Field(
        None,
        description=(
            "The venue's own option product code, when it differs from the ticker. "
            "Eurex lists BMW.DE's options under the product 'BMW'. Only needed when "
            "stripping the exchange suffix does not give the right code."
        ),
    )


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
    venue: Venue
    venue_product: str | None
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
