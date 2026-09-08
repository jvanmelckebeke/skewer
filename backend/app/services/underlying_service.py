"""Adding, listing and reporting on tracked underlyings."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.venue import Venue, spec_for
from app.models import Underlying
from app.repositories.contract_repo import ContractRepository
from app.repositories.group_repo import GroupRepository
from app.repositories.quote_repo import QuoteRepository
from app.repositories.underlying_repo import UnderlyingRepository
from app.services.market_calendar import is_market_open, local_now
from app.services.option_providers import for_venue

logger = logging.getLogger(__name__)


async def list_underlyings(db: AsyncSession) -> list[Underlying]:
    return await UnderlyingRepository(db).list_all()


# The order venues are tried in when the caller does not name one. OCC first
# because it is the larger universe and the cheaper check; Eurex second.
#
# Euronext is deliberately absent. Its API has no chain endpoint, so even
# validating a ticker costs a burst of per-contract probes against a licensed
# gateway — far too expensive to run speculatively on every add. Euronext
# underlyings must name their venue explicitly.
_DETECT_ORDER = (Venue.OCC, Venue.EUREX)


async def add_underlying(
    db: AsyncSession, symbol: str, group_id: int | None = None,
    venue: Venue | None = None, venue_product: str | None = None,
) -> Underlying:
    """Validate a ticker against a provider, then start tracking it.

    Validation is a real fetch rather than a regex, because the failure it
    exists to catch is a tracked underlying that silently collects nothing
    forever.

    When ``venue`` is omitted the venues are tried in turn and the first that
    actually returns a chain wins. That is what makes ``NVDA`` and ``BMW.DE``
    both work without the user having to know that one is OPRA and the other
    is Eurex — which is the whole point of routing by venue rather than by a
    global provider setting.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(422, "Symbol must not be empty")

    repo = UnderlyingRepository(db)
    existing = await repo.get_by_symbol(symbol)
    if existing is not None:
        raise HTTPException(409, f"{symbol} is already tracked")

    candidates = (venue,) if venue is not None else _DETECT_ORDER
    resolved: Venue | None = None
    name = ""
    for candidate in candidates:
        try:
            ok, found = await for_venue(candidate).validate(symbol)
        except Exception:
            logger.warning("Validation failed for %s on %s", symbol, candidate, exc_info=True)
            continue
        if ok:
            resolved, name = candidate, found
            break

    if resolved is None:
        tried = ", ".join(spec_for(v).label for v in candidates)
        # A venue that is simply unconfigured is a different problem from one
        # that has no chain, and saying so saves the user from hunting for a
        # ticker error that is really a missing key.
        unconfigured = [
            spec_for(v).label for v in candidates
            if getattr(for_venue(v), "configured", True) is False
        ]
        detail = f"No option chain available for {symbol}. Tried: {tried}."
        if unconfigured:
            detail += (
                f" Note that {', '.join(unconfigured)} has no credentials configured"
                f" (set EURONEXT_AUTH_KEY), so it was queried but could not answer."
            )
        detail += (
            " Yahoo covers US listings (OPRA) only; Eurex covers German and other"
            " European single-stock options; Euronext covers Amsterdam, Brussels,"
            " Paris, Lisbon and Oslo. The venue's product code may differ from the"
            " ticker, so pass venue_product (e.g. 'DBRU:ABI') if so."
        )
        raise HTTPException(422, detail)

    underlying = await repo.create(
        symbol, name, currency=spec_for(resolved).currency,
        venue=resolved, venue_product=venue_product,
    )

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
