"""Adding, listing and reporting on tracked underlyings."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Underlying
from app.repositories.contract_repo import ContractRepository
from app.repositories.group_repo import GroupRepository
from app.repositories.quote_repo import QuoteRepository
from app.repositories.underlying_repo import UnderlyingRepository
from app.services.market_calendar import is_market_open, local_now
from app.services.option_providers import get_option_provider

logger = logging.getLogger(__name__)


async def list_underlyings(db: AsyncSession) -> list[Underlying]:
    return await UnderlyingRepository(db).list_all()


async def add_underlying(db: AsyncSession, symbol: str, group_id: int | None = None) -> Underlying:
    """Validate a ticker against the provider, then start tracking it.

    Validation is a real fetch rather than a regex. The failure it exists to
    catch is the one you already identified — Yahoo publishes chains for US
    listings only — and the difference between rejecting ``ASML.AS`` at add
    time and accepting it is a tracked underlying that silently collects
    nothing forever.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(422, "Symbol must not be empty")

    repo = UnderlyingRepository(db)
    existing = await repo.get_by_symbol(symbol)
    if existing is not None:
        raise HTTPException(409, f"{symbol} is already tracked")

    provider = get_option_provider()
    ok, name = await provider.validate(symbol)
    if not ok:
        raise HTTPException(
            422,
            f"No option chain available for {symbol}. Yahoo publishes options for US "
            f"listings only — European and most non-US tickers have no chain.",
        )

    underlying = await repo.create(symbol, name)

    group_repo = GroupRepository(db)
    group = await group_repo.get(group_id) if group_id else await group_repo.get_default()
    if group is None:
        raise HTTPException(500, "No default group exists to add the underlying to")
    group.underlyings.append(underlying)
    await db.commit()
    await db.refresh(underlying)
    return underlying


async def update_underlying(db: AsyncSession, symbol: str, data: dict) -> Underlying:
    repo = UnderlyingRepository(db)
    row = await repo.get_by_symbol(symbol)
    if row is None:
        raise HTTPException(404, f"Underlying {symbol.upper()!r} is not tracked")
    if "is_active" in data and data["is_active"] is not None:
        row.is_active = data["is_active"]
    if "dividend_yield" in data and data["dividend_yield"] is not None:
        row.dividend_yield = data["dividend_yield"]
        # A user-set yield is never overwritten by a later provider guess —
        # same provenance rule fibenchi applies to asset type and currency.
        row.dividend_yield_is_manual = True
    await db.commit()
    await db.refresh(row)
    return row


async def delete_underlying(db: AsyncSession, symbol: str) -> None:
    """Stop tracking and delete the underlying, its contracts and its history.

    Unlike fibenchi's soft delete, this is a real cascade. The reason for the
    difference: fibenchi preserves asset rows because pseudo-ETFs reference
    them, so deletion would break a relationship. Skewer has no such
    references — an option contract belongs to exactly one underlying and to
    nothing else. Pausing without data loss is what ``is_active=false`` and
    group removal are for, and both are reachable from the API.
    """
    repo = UnderlyingRepository(db)
    row = await repo.get_by_symbol(symbol)
    if row is None:
        raise HTTPException(404, f"Underlying {symbol.upper()!r} is not tracked")
    await db.delete(row)
    await db.commit()


async def status(db: AsyncSession, symbol: str) -> dict:
    """Collection health for one underlying."""
    repo = UnderlyingRepository(db)
    row = await repo.get_by_symbol(symbol)
    if row is None:
        raise HTTPException(404, f"Underlying {symbol.upper()!r} is not tracked")

    today = local_now().date()
    contract_repo = ContractRepository(db)
    live = await contract_repo.count_for(row.id, live_on=today)
    total = await contract_repo.count_for(row.id)
    last_slot = await QuoteRepository(db).latest_ts(row.id)

    since = None
    if row.last_polled_at is not None:
        polled = row.last_polled_at
        if polled.tzinfo is None:
            polled = polled.replace(tzinfo=timezone.utc)
        since = (datetime.now(tz=timezone.utc) - polled).total_seconds() / 60.0

    return {
        "symbol": row.symbol,
        "is_active": row.is_active,
        "live_contracts": live,
        "total_contracts": total,
        "last_polled_at": row.last_polled_at,
        "last_slot": last_slot,
        "last_eod_snapshot_on": row.last_eod_snapshot_on,
        "minutes_since_poll": since,
        "market_open": is_market_open(),
    }
