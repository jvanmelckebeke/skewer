from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

group_underlyings = Table(
    "group_underlyings",
    Base.metadata,
    Column("group_id", ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    Column("underlying_id", ForeignKey("underlyings.id", ondelete="CASCADE"), primary_key=True),
)


class Group(Base):
    """A named collection of underlyings — ported from fibenchi, which is the
    feature you said you wanted to keep. Same default-group convention: exactly
    one row carries ``is_default`` (the seeded 'Watchlist'), and removing an
    underlying from it stops collection without destroying accumulated history.
    """

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    underlyings: Mapped[list["Underlying"]] = relationship(
        secondary=group_underlyings, lazy="selectin"
    )


from app.models.underlying import Underlying  # noqa: E402, F401
