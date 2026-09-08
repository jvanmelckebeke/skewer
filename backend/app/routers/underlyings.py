from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.underlying import (
    UnderlyingCreate,
    UnderlyingResponse,
    UnderlyingStatus,
    UnderlyingUpdate,
)
from app.services import underlying_service

router = APIRouter(prefix="/api/underlyings", tags=["underlyings"])


@router.get("", response_model=list[UnderlyingResponse], summary="List tracked underlyings")
async def list_underlyings(db: AsyncSession = Depends(get_db)):
    return await underlying_service.list_underlyings(db)


@router.post("", response_model=UnderlyingResponse, status_code=201, summary="Track an underlying")
async def add_underlying(data: UnderlyingCreate, db: AsyncSession = Depends(get_db)):
    """Validate the ticker has a listed option chain, then start collecting it.

    Rejects with 422 when the provider has no chain — which is every European
    listing, since Yahoo publishes options for US tickers only.
    """
    return await underlying_service.add_underlying(db, data.symbol, data.group_id)


@router.get("/{symbol}", response_model=UnderlyingResponse, summary="Get one underlying")
async def get_underlying(symbol: str, db: AsyncSession = Depends(get_db)):
    from app.services.chain_service import get_underlying_or_404

    return await get_underlying_or_404(db, symbol)


@router.patch("/{symbol}", response_model=UnderlyingResponse, summary="Update an underlying")
async def update_underlying(symbol: str, data: UnderlyingUpdate, db: AsyncSession = Depends(get_db)):
    return await underlying_service.update_underlying(db, symbol, data.model_dump(exclude_unset=True))


@router.delete("/{symbol}", status_code=204, summary="Stop tracking and delete history")
async def delete_underlying(symbol: str, db: AsyncSession = Depends(get_db)):
    """Delete the underlying, its contracts and all collected quotes.

    To stop collecting *without* losing data, set `is_active=false` or remove
    it from every group instead.
    """
    await underlying_service.delete_underlying(db, symbol)


@router.get("/{symbol}/status", response_model=UnderlyingStatus, summary="Collection health")
async def underlying_status(symbol: str, db: AsyncSession = Depends(get_db)):
    """Is collection actually working for this underlying?"""
    return await underlying_service.status(db, symbol)
