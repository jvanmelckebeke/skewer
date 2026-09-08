from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group


class GroupRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> list[Group]:
        result = await self.db.execute(select(Group).order_by(Group.position, Group.id))
        return list(result.scalars().all())

    async def get(self, group_id: int) -> Group | None:
        return await self.db.get(Group, group_id)

    async def get_default(self) -> Group | None:
        result = await self.db.execute(select(Group).where(Group.is_default.is_(True)))
        return result.scalars().first()

    async def create(self, name: str, description: str | None, icon: str | None) -> Group:
        row = Group(name=name, description=description, icon=icon)
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def delete(self, group: Group) -> None:
        await self.db.delete(group)
        await self.db.commit()
