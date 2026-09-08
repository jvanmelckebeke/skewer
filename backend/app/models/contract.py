from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain.contracts import Right
from app.domain.venue import ExerciseStyle, Venue


class OptionContract(Base):
    """The dimension table of the star schema: one row per contract, ever.

    This is the single most important structural decision in skewer. Strike,
    expiration and right are *immutable properties of the contract's identity*.
    On OCC they are encoded in the symbol itself; on Eurex they are published
    as fields against an opaque ISIN. Storing them here, once, means
    the fact table (``option_quotes``) carries an ``int`` foreign key and
    nothing else descriptive. That is what keeps a tick row ~40 bytes instead
    of ~120, and it is precisely the normalisation a tag-based time-series
    store cannot express without exploding series cardinality.

    Rows accumulate and are never deleted on expiry: an expired contract's
    quote history is the historical record, and dropping the dimension row
    would orphan it. ``last_seen_at`` marks contracts the provider has stopped
    listing, which is how polling learns to skip them.
    """

    __tablename__ = "option_contracts"
    __table_args__ = (
        # The chain query: "every live contract for this underlying, near-dated
        # first". Covers both the sync loop's per-underlying scan and the API's
        # per-expiration slice.
        Index("ix_contract_underlying_exp", "underlying_id", "expiration", "strike"),
        # Cadence bucketing scans by expiry across all underlyings.
        Index("ix_contract_expiration", "expiration"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    underlying_id: Mapped[int] = mapped_column(
        ForeignKey("underlyings.id", ondelete="CASCADE"), index=True
    )

    # The venue's own identifier is the natural key; the surrogate ``id``
    # exists only because a 4-byte int in the fact table beats a 21-byte
    # string. For OCC this is the bare 21-character symbol, unchanged from
    # when the column was called ``occ_symbol``. For Eurex it is an
    # ``EUX:``-prefixed ISIN. The prefix is what makes the two namespaces
    # provably non-colliding in this unique index.
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    # Which market listed it. Drives the expiry instant (16:00 New York vs
    # 17:30 Berlin), the symbology, and which provider refreshes it.
    venue: Mapped[Venue] = mapped_column(
        Enum(Venue, name="option_venue", values_callable=lambda e: [m.value for m in e]),
        default=Venue.OCC,
        server_default=Venue.OCC.value,
    )

    # Recorded, not assumed: Eurex lists both styles on the same underlying at
    # the same expiry. It does not currently change pricing — see the note on
    # ExerciseStyle in domain/venue.py, which explains why BSM is applied to
    # American contracts on both venues — but storing it now means the day a
    # binomial path lands there is no backfill to do.
    exercise_style: Mapped[ExerciseStyle] = mapped_column(
        Enum(
            ExerciseStyle,
            name="option_exercise_style",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ExerciseStyle.AMERICAN,
        server_default=ExerciseStyle.AMERICAN.value,
    )

    expiration: Mapped[date] = mapped_column(Date)
    strike: Mapped[float] = mapped_column(Numeric(14, 4, asdecimal=False))
    # values_callable is required, not cosmetic: SQLAlchemy stores a Python
    # enum's *name* by default ("CALL"), while the migration creates the
    # PostgreSQL type from its *values* ('call'/'put'). Without this the insert
    # fails with InvalidTextRepresentationError. Lowercase values are also what
    # the API serialises, so the DB and the JSON agree.
    right: Mapped[Right] = mapped_column(
        Enum(Right, name="option_right", values_callable=lambda e: [m.value for m in e])
    )

    # The share multiplier: 100 for a standard US or Eurex equity option, 10
    # for a US mini. Stored as the integer rather than as Yahoo's 'REGULAR' /
    # 'MINI' label, because the label is a US-ism that Eurex has no analogue
    # for, while the number means the same thing on every venue. Affects
    # nothing in the greeks (delta is per share) but everything in position
    # sizing, so it is recorded rather than assumed.
    contract_size: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    currency: Mapped[str] = mapped_column(String(10), default="USD", server_default="USD")

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    underlying: Mapped["Underlying"] = relationship(back_populates="contracts")


from app.models.underlying import Underlying  # noqa: E402, F401
