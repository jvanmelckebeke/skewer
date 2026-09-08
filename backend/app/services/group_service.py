"""Group CRUD — ported from fibenchi, over underlyings instead of assets."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group, Underlying
from app.repositories.group_repo import GroupRepository


async def list_groups(db: AsyncSession) -> list[Group]:
    return await GroupRepository(db).list_all()


async def get_group(db: AsyncSession, group_id: int) -> Group:
    group = await GroupRepository(db).get(group_id)
    if group is None:
        raise HTTPException(404, f"Group {group_id} not found")
    return group


async def create_group(db: AsyncSession, name: str, description, icon) -> Group:
    return await GroupRepository(db).create(name, description, icon)


async def update_group(db: AsyncSession, group_id: int, data: dict) -> Group:
    group = await get_group(db, group_id)
    if group.is_default and "name" in data:
        raise HTTPException(422, "The default group cannot be renamed")
    for key, value in data.items():
        if value is not None:
            setattr(group, key, value)
    await db.commit()
    await db.refresh(group)
    return group


async def delete_group(db: AsyncSession, group_id: int) -> None:
    group = await get_group(db, group_id)
    if group.is_default:
        raise HTTPException(422, "The default group cannot be deleted")
    await GroupRepository(db).delete(group)


async def add_underlyings(db: AsyncSession, group_id: int, underlying_ids: list[int]) -> Group:
    group = await get_group(db, group_id)
    existing = {u.id for u in group.underlyings}
    for uid in underlying_ids:
        if uid in existing:
            continue
        row = await db.get(Underlying, uid)
        if row is None:
            raise HTTPException(404, f"Underlying {uid} not found")
        group.underlyings.append(row)
    await db.commit()
    await db.refresh(group)
    return group


async def remove_underlying(db: AsyncSession, group_id: int, underlying_id: int) -> Group:
    """Remove an underlying from a group.

    Removing it from its last group stops collection — group membership is the
    switch — while every stored quote is preserved. This is the non-destructive
    way to stop tracking something.
    """
    group = await get_group(db, group_id)
    group.underlyings = [u for u in group.underlyings if u.id != underlying_id]
    await db.commit()
    await db.refresh(group)
    return group
