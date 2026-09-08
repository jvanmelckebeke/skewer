import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.background_tasks import all_tasks
from app.config import settings
from app.database import get_db
from app.domain.cadence import CADENCE_BUCKETS
from app.services.market_calendar import is_market_open, local_now

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", summary="Health check")
async def health():
    return {"status": "ok"}


@router.get("/system/collection", summary="Collection configuration and schedule")
async def collection_status(db: AsyncSession = Depends(get_db)):
    """What the collector is configured to do, and whether it should be running.

    Exists because "why do I have no data" is the first question a collector
    ever gets asked, and the answer is usually one of: market closed, no
    underlyings in a group, or a disabled job.
    """
    from app.repositories.underlying_repo import UnderlyingRepository

    active = await UnderlyingRepository(db).list_active()
    return {
        "now_utc": datetime.now(tz=timezone.utc),
        "now_market": local_now(),
        "market_open": is_market_open(),
        "base_poll_minutes": settings.base_poll_minutes,
        "active_underlyings": [u.symbol for u in active],
        "cadence_buckets": [
            {
                "name": b.name,
                "max_dte": b.max_dte,
                "interval_minutes": b.interval_minutes or None,
                "note": "end-of-day snapshot only" if b.interval_minutes <= 0 else None,
            }
            for b in CADENCE_BUCKETS
        ],
        "jobs": [
            {"id": t.id, "trigger": str(t.resolve_trigger()), "enabled": t.resolve_trigger() is not None}
            for t in all_tasks()
        ],
    }
