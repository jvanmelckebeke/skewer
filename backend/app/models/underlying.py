from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Underlying(Base):
    """A tracked equity whose option chain we collect.

    The analogue of fibenchi's ``Asset``, deliberately thinner: skewer does not
    store the underlying's OHLCV history (fibenchi already does that job well).
    It stores only what option pricing needs — the live spot, the dividend
    yield, and the currency.
    """

    __tablename__ = "underlyings"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    currency: Mapped[str] = mapped_column(String(10), default="USD", server_default="USD")

    # Continuous dividend yield 'q' in Black-Scholes-Merton. Stored per
    # underlying rather than assumed zero: on a 2% yielder a year out, ignoring
    # it misprices delta by roughly the same 2%, which is far from noise when
    # the whole point is a delta-based view of the chain. Defaults to 0 and is
    # refreshed from the provider, but a user override sticks (see
    # ``dividend_yield_is_manual``).
    dividend_yield: Mapped[float] = mapped_column(
        Numeric(8, 6, asdecimal=False), default=0.0, server_default="0"
    )
    dividend_yield_is_manual: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Whether chain collection is currently running for this underlying.
    # Pausing is not deleting: history is kept, polling simply stops.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # Denormalised freshness markers, maintained by the sync job. They answer
    # "is collection healthy?" without scanning the fact table, which is the
    # expensive question to ask of a hypertable.
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_eod_snapshot_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    contracts: Mapped[list["OptionContract"]] = relationship(
        back_populates="underlying", cascade="all, delete-orphan"
    )


from app.models.contract import OptionContract  # noqa: E402, F401
