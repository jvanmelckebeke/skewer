from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.group import GroupAddUnderlyings, GroupCreate, GroupResponse, GroupUpdate
from app.services import group_service

router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.get("", response_model=list[GroupResponse], summary="List all groups")
async def list_groups(db: AsyncSession = Depends(get_db)):
    return await group_service.list_groups(db)


@router.post("", response_model=GroupResponse, status_code=201, summary="Create a group")
async def create_group(data: GroupCreate, db: AsyncSession = Depends(get_db)):
    return await group_service.create_group(db, data.name, data.description, data.icon)


@router.get("/{group_id}", response_model=GroupResponse, summary="Get a group")
async def get_group(group_id: int, db: AsyncSession = Depends(get_db)):
    return await group_service.get_group(db, group_id)


@router.put("/{group_id}", response_model=GroupResponse, summary="Update a group")
async def update_group(group_id: int, data: GroupUpdate, db: AsyncSession = Depends(get_db)):
    return await group_service.update_group(db, group_id, data.model_dump(exclude_unset=True))


@router.delete("/{group_id}", status_code=204, summary="Delete a group")
async def delete_group(group_id: int, db: AsyncSession = Depends(get_db)):
    await group_service.delete_group(db, group_id)


@router.post("/{group_id}/underlyings", response_model=GroupResponse, summary="Add underlyings to a group")
async def add_underlyings(group_id: int, data: GroupAddUnderlyings, db: AsyncSession = Depends(get_db)):
    return await group_service.add_underlyings(db, group_id, data.underlying_ids)


@router.delete(
    "/{group_id}/underlyings/{underlying_id}",
    response_model=GroupResponse,
    summary="Remove an underlying from a group",
)
async def remove_underlying(group_id: int, underlying_id: int, db: AsyncSession = Depends(get_db)):
    """Removing an underlying from its last group stops collection but keeps
    every quote already stored."""
    return await group_service.remove_underlying(db, group_id, underlying_id)
