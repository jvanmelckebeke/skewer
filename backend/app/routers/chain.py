from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.chain import (
    ChainResponse,
    ContractSeriesResponse,
    MetricDescriptor,
    SessionProfileResponse,
    SmileResponse,
    TermStructureResponse,
)
from app.services import chain_service
from app.services.compute.analytics import METRIC_REGISTRY

router = APIRouter(prefix="/api/chains", tags=["chains"])


@router.get("/metrics", response_model=list[MetricDescriptor], summary="Available chain metrics")
async def list_metrics():
    """Every metric in the registry, so a client can discover them rather than
    hard-coding a list."""
    return [
        {"id": m.id, "label": m.label, "unit": m.unit, "description": m.description}
        for m in METRIC_REGISTRY.values()
    ]


@router.get("/{symbol}", response_model=ChainResponse, summary="Option chain snapshot")
async def get_chain(
    symbol: str,
    at: datetime | None = Query(None, description="Exact stored slot; latest when omitted."),
    expiration: date | None = Query(None, description="Restrict to one expiration."),
    db: AsyncSession = Depends(get_db),
):
    """A simultaneous cross-section of the chain, with greeks and metrics.

    Contracts in slower cadence buckets are absent from slots they were not
    sampled in — that is the honest representation of "not observed", and no
    value is interpolated in.
    """
    return await chain_service.chain_view(db, symbol, at=at, expiration=expiration)


@router.get("/{symbol}/smile", response_model=SmileResponse, summary="IV smile for one expiration")
async def get_smile(
    symbol: str,
    expiration: date = Query(..., description="Which expiration to plot."),
    at: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await chain_service.smile_view(db, symbol, expiration, at=at)


@router.get(
    "/{symbol}/session",
    response_model=SessionProfileResponse,
    summary="Per-strike session profile for one expiration",
)
async def get_session_profile(
    symbol: str,
    expiration: date | None = Query(
        None, description="Which expiration to profile; the session's nearest-dated when omitted."
    ),
    session: date | None = Query(
        None, description="Session date (exchange-local); the most recent stored one when omitted."
    ),
    db: AsyncSession = Depends(get_db),
):
    """Quantiles per strike across every slot captured in one session.

    The smile as a range rather than a photograph: the median is where implied
    volatility sat, and the interquartile spread is how much the strike was
    repriced over the day. Strikes with no identifiable volatility all session
    still appear, with `priced` at zero and every quantile null — the chain is
    the record of what exists, and an absent strike would misrepresent it as
    unlisted rather than unpriced.
    """
    return await chain_service.session_profile(db, symbol, expiration, session=session)


@router.get("/{symbol}/term", response_model=TermStructureResponse, summary="ATM IV term structure")
async def get_term_structure(
    symbol: str, at: datetime | None = Query(None), db: AsyncSession = Depends(get_db)
):
    return await chain_service.term_view(db, symbol, at=at)


@router.get(
    "/contracts/{occ_symbol}",
    response_model=ContractSeriesResponse,
    summary="One contract's quote history",
)
async def get_contract_series(
    occ_symbol: str,
    days: int = Query(5, ge=1, le=90, description="Lookback window in days."),
    db: AsyncSession = Depends(get_db),
):
    return await chain_service.contract_series(db, occ_symbol, days=days)
