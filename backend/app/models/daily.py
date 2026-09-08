from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OptionDailyStat(Base):
    """One settled row per contract per session — the daily half of the split.

    Open interest is the reason this table exists. It is published by the OCC
    overnight and changes exactly once per day, so carrying it on every
    intraday tick would store the same integer ~78 times a session for no
    information gain. Splitting it out removes that redundancy from the hot
    table entirely, and gives the natural home for end-of-day settled values
    (closing mark, session volume, closing IV) that the intraday grid cannot
    represent because the last poll is not the close.

    This table is small — one row per contract per session, so a full NVDA
    chain contributes ~3.3k rows/day rather than ~85k — which makes it the
    right place for the long-horizon queries: IV rank, OI build-up, put/call
    ratio history, max pain over time.
    """

    __tablename__ = "option_daily_stats"
    __table_args__ = (
        Index("ix_daily_session_contract", "session_date", "contract_id"),
    )

    contract_id: Mapped[int] = mapped_column(
        ForeignKey("option_contracts.id", ondelete="CASCADE"), primary_key=True
    )
    # Exchange-local session date (America/New_York), not a UTC date — the
    # distinction matters because the EOD snapshot runs at 21:10 UTC, which is
    # already the next calendar day for part of the year in other zones.
    session_date: Mapped[date] = mapped_column(Date, primary_key=True)

    open_interest: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    close_bid: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    close_ask: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    close_mark: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    close_last: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)

    close_spot: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False), nullable=True)
    close_iv: Mapped[float | None] = mapped_column(Numeric(10, 6, asdecimal=False), nullable=True)
    close_iv_provider: Mapped[float | None] = mapped_column(Numeric(10, 6, asdecimal=False), nullable=True)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
