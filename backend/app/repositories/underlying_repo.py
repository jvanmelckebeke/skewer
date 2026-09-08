from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group, Underlying, group_underlyings


class UnderlyingRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> list[Underlying]:
        result = await self.db.execute(select(Underlying).order_by(Underlying.symbol))
        return list(result.scalars().all())

    async def list_active(self) -> list[Underlying]:
        """Underlyings that are collecting *and* belong to at least one group.

        Group membership is the collection switch, exactly as in fibenchi:
        removing an underlying from every group stops the polling without
        destroying the history it already accumulated.
        """
        result = await self.db.execute(
            select(Underlying)
            .join(group_underlyings, group_underlyings.c.underlying_id == Underlying.id)
            .where(Underlying.is_active.is_(True))
            .distinct()
            .order_by(Underlying.symbol)
        )
        return list(result.scalars().all())

    async def get_by_symbol(self, symbol: str) -> Underlying | None:
        result = await self.db.execute(
            select(Underlying).where(Underlying.symbol == symbol.upper())
        )
        return result.scalar_one_or_none()

    async def get(self, underlying_id: int) -> Underlying | None:
        return await self.db.get(Underlying, underlying_id)

    async def create(self, symbol: str, name: str, currency: str = "USD") -> Underlying:
        row = Underlying(symbol=symbol.upper(), name=name, currency=currency)
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def mark_polled(self, underlying_id: int, at: datetime) -> None:
        await self.db.execute(
            update(Underlying).where(Underlying.id == underlying_id).values(last_polled_at=at)
        )
        await self.db.commit()

    async def mark_eod(self, underlying_id: int, on: date) -> None:
        await self.db.execute(
            update(Underlying).where(Underlying.id == underlying_id).values(last_eod_snapshot_on=on)
        )
        await self.db.commit()

    async def default_group(self) -> Group | None:
        result = await self.db.execute(select(Group).where(Group.is_default.is_(True)))
        return result.scalars().first()
