from datetime import date, datetime

from pydantic import BaseModel, Field

from app.domain.venue import Venue


class ContractQuote(BaseModel):
    """One contract at one instant, with everything derived filled in.

    Greeks are nullable throughout, and that is a feature: a contract whose IV
    could not be identified has no delta either, and returning 0.0 would be a
    lie that plots.
    """

    symbol: str = Field(..., description="The contract's key: an OCC symbol, or a venue-prefixed one such as EUX:<ISIN>.")
    expiration: date
    strike: float
    right: str
    dte: int

    bid: float | None
    ask: float | None
    mark: float | None
    last: float | None
    last_trade_at: datetime | None = Field(
        None, description="When `last` actually traded — often days or weeks ago."
    )
    volume: int
    open_interest: int | None

    iv: float | None = Field(None, description="Skewer's own implied volatility, solved from mark.")
    iv_provider: float | None = Field(None, description="The provider's IV, for comparison only.")
    iv_status: str = Field("ok", description="Why iv is present or absent (see compute/iv.IVStatus).")

    delta: float | None = None
    gamma: float | None = None
    vega: float | None = Field(None, description="Per one volatility point (1%).")
    theta: float | None = Field(None, description="Per calendar day.")
    rho: float | None = None
    model_price: float | None = None
    moneyness: float | None = Field(None, description="log(strike / spot).")


class ChainResponse(BaseModel):
    symbol: str
    ts: datetime
    spot: float | None
    expirations: list[date]
    contracts: list[ContractQuote]
    metrics: dict[str, float | None] = {}
    # The q the greeks on this chain were solved against. Carried on the
    # response because it is a pricing input the reader cannot otherwise see,
    # and a delta computed at the wrong yield looks exactly like one computed
    # at the right yield.
    dividend_yield: float | None = None
    # True when a human set it, false when it was read off put-call parity.
    # The distinction matters: a measurement moves with the next poll.
    dividend_yield_is_manual: bool = False


class SmileResponse(BaseModel):
    symbol: str
    ts: datetime
    expiration: date
    spot: float | None
    moneyness: list[float]
    strike: list[float]
    iv: list[float]
    delta: list[float]


class TermPoint(BaseModel):
    expiration: date
    atm_iv: float
    contracts: int


class TermStructureResponse(BaseModel):
    symbol: str
    ts: datetime
    spot: float | None
    points: list[TermPoint]


class ChainSummaryRow(BaseModel):
    """One tracked underlying, reduced to the numbers a list can show.

    Every registered metric appears in ``metrics`` whether or not it could be
    computed, so a client can render a fixed set of columns without checking
    which keys came back. A missing value is null, and null here means the
    chain could not answer, not that the answer was zero.
    """

    symbol: str
    name: str
    venue: Venue
    currency: str
    is_active: bool

    # Enough to flag a stalled collector without a status request per row.
    # market_open is the US session gate, the same approximation
    # UnderlyingStatus already makes: a Eurex name is judged by NYSE hours.
    market_open: bool
    minutes_since_poll: float | None
    is_stale: bool

    # Null until the first poll lands. An underlying added a minute ago is a
    # normal row with nothing in it, not an error.
    ts: datetime | None
    spot: float | None
    metrics: dict[str, float | None]


class MetricDescriptor(BaseModel):
    id: str
    label: str
    unit: str
    description: str


class ContractSeriesPoint(BaseModel):
    ts: datetime
    mark: float | None
    bid: float | None
    ask: float | None
    iv: float | None
    spot: float | None
    volume: int


class ContractSeriesResponse(BaseModel):
    symbol: str = Field(..., description="The contract's key: an OCC symbol, or a venue-prefixed one such as EUX:<ISIN>.")
    expiration: date
    strike: float
    right: str
    points: list[ContractSeriesPoint]


class SessionStrikePoint(BaseModel):
    """One contract's behaviour across a whole session.

    Quantiles, not a single value: the point of a session view is *how much a
    strike moved*, and a median alone throws that away. Every quantile is
    nullable because a contract can be sampled all day and never once carry an
    identifiable volatility.
    """

    symbol: str = Field(..., description="The contract's key: an OCC symbol, or a venue-prefixed one such as EUX:<ISIN>.")
    strike: float
    right: str
    samples: int = Field(..., description="Slots this contract was captured in.")
    priced: int = Field(..., description="Of those, how many yielded an IV.")

    iv_p25: float | None = None
    iv_p50: float | None = None
    iv_p75: float | None = None
    iv_min: float | None = None
    iv_max: float | None = None

    mark_p50: float | None = None
    spread_p50: float | None = Field(
        None,
        description="Median bid-ask spread as a fraction of mark. Relative because "
        "an absolute width is not comparable across strikes.",
    )
    volume: int = Field(0, description="Session volume, as last reported (it is cumulative).")


class SessionSummary(BaseModel):
    """A session with stored data, and how many slots it holds."""

    session_date: date
    slots: int


class SessionProfileResponse(BaseModel):
    symbol: str
    session_date: date
    expiration: date
    slots: int = Field(..., description="Distinct captured slots in the session.")
    spot_low: float | None = None
    spot_high: float | None = None
    available_sessions: list[SessionSummary] = Field(
        default_factory=list, description="Sessions with stored data, most recent first."
    )
    available_expirations: list[date] = Field(
        default_factory=list,
        description="Expirations with stored quotes in this session — not the live chain's, "
        "which no longer lists anything that has since expired.",
    )
    points: list[SessionStrikePoint] = []
