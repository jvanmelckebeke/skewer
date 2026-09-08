from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OptionQuote(Base):
    """The fact table: one row per contract per captured slot. A hypertable.

    Deliberately narrow. Everything derivable is derived on read rather than
    stored: greeks are a pure function of (spot, strike, tenor, IV, rate,
    yield), and all five inputs are recoverable — so storing delta/gamma/theta/
    vega per tick would roughly triple the width of the largest table in the
    system to cache arithmetic that costs microseconds.

    ``iv`` is the one exception, and the exception proves the rule: it is *not*
    cheaply derivable (it is a root-find over the pricing function), and
    re-solving it on read for a 3,000-contract chain across a day of history
    would dominate query time. So the expensive scalar is stored and the cheap
    ones are not.

    There is no surrogate primary key. The natural key ``(contract_id, ts)`` is
    also the upsert conflict target and the hypertable's partition-compatible
    PK — and it sidesteps the sequence-exhaustion failure fibenchi hit on its
    intraday table, where ON CONFLICT burned a sequence value per *attempted*
    row and ran an int32 sequence dry.
    """

    __tablename__ = "option_quotes"

    contract_id: Mapped[int] = mapped_column(
        ForeignKey("option_contracts.id", ondelete="CASCADE"), primary_key=True
    )
    # Snapped to the poll grid (app/domain/cadence.floor_to_slot) so every
    # contract captured in one poll shares a timestamp — a stored cross-section
    # is genuinely simultaneous, which is what makes an IV surface meaningful.
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    # Nullable on purpose. Yahoo returns bid/ask as 0.0 outside regular hours
    # for the overwhelming majority of contracts (2,948 of NVDA's 3,286 while
    # the market was in PRE). A zero bid is "no quote", not "worth nothing",
    # and conflating the two would poison every mid-price and every solved IV.
    # The provider layer maps those zeros to NULL; a NULL here means "not
    # quoted", which is a fact worth storing.
    bid: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    ask: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)

    # Mid when a two-sided quote exists, else NULL. Stored rather than computed
    # on read because the *reason* it is NULL (crossed book, one-sided, closed
    # market) is resolved once at ingest with the raw payload in hand.
    mark: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)

    # Last traded price, and when that trade happened. The timestamp is the
    # honest part: Yahoo will happily report a lastPrice whose trade was weeks
    # ago (observed: NVDA contracts last traded 2026-07-13 quoted live). Any
    # consumer that falls back to last must be able to see how stale it is.
    last: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    last_trade_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    volume: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")

    # Spot at capture, denormalised from the same provider response that
    # produced this row. Denormalisation is justified here: greeks need spot
    # *as of this instant*, and joining to a separately-sampled spot series
    # would reintroduce exactly the time skew that the single-request fetch
    # eliminates. Four bytes to make every greek reproducible offline.
    spot: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)

    # Our own IV, re-solved from ``mark`` (see services/compute/iv.py), and the
    # provider's for comparison. Yahoo's is kept because disagreement between
    # the two is a useful data-quality signal, not because it is trusted:
    # observed values include 1e-05 on deep-ITM strikes, which is not a
    # volatility, it is a solver giving up.
    iv: Mapped[float | None] = mapped_column(Numeric(10, 6, asdecimal=False), nullable=True)
    iv_provider: Mapped[float | None] = mapped_column(Numeric(10, 6, asdecimal=False), nullable=True)

    # Why iv is NULL / how it was obtained. Cheap to store, and it turns
    # "missing IV" from a mystery into a diagnosable category.
    iv_status: Mapped[str] = mapped_column(String(16), default="ok", server_default="ok")


class UnderlyingQuote(Base):
    """Spot price series for tracked underlyings. Also a hypertable.

    ``OptionQuote.spot`` already carries spot per tick, so this is not needed
    for greeks — it exists because the underlying's own path is the reference
    series for realised volatility, IV-rank, and any "how did the surface move
    when the stock moved" question. Same grid, same slot alignment.
    """

    __tablename__ = "underlying_quotes"

    underlying_id: Mapped[int] = mapped_column(
        ForeignKey("underlyings.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    price: Mapped[float] = mapped_column(Numeric(14, 4, asdecimal=False))
    bid: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    ask: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    previous_close: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    market_state: Mapped[str | None] = mapped_column(String(12), nullable=True)
