"""Fetch a chain, decide what to keep, solve IV, persist.

The one place the capture policy is enforced. Reading top to bottom gives the
whole collection story:

    fetch full chain (one request)
      -> register any newly listed contracts
      -> select which contracts this tick persists (cadence)
      -> solve IV on the survivors
      -> upsert quotes + the underlying's spot

The end-of-day path is the same function with ``full=True``, which bypasses
cadence selection so every contract — LEAPS included — gets exactly one settled
row per session, and additionally writes open interest to the daily table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.cadence import floor_to_slot, should_persist
from app.repositories.contract_repo import ContractRepository
from app.repositories.quote_repo import QuoteRepository
from app.repositories.underlying_repo import UnderlyingRepository
from app.services.compute.bs import CALL, PUT
from app.services.compute.iv import IVStatus, implied_vol, mid_price
from app.services.option_providers import ChainUnavailable, RawChain, get_option_provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SyncResult:
    """What one underlying's poll actually did — the unit the jobs log."""

    symbol: str
    fetched: int
    persisted: int
    new_contracts: int
    iv_solved: int
    daily_rows: int
    slot: datetime | None
    skipped_reason: str | None = None


async def sync_underlying(
    db: AsyncSession, underlying_id: int, symbol: str, dividend_yield: float = 0.0,
    full: bool = False, now: datetime | None = None,
) -> SyncResult:
    """Poll one underlying and persist the selected slice of its chain."""
    now = now or datetime.now(tz=timezone.utc)
    provider = get_option_provider()

    try:
        chain = await provider.fetch_chain(symbol)
    except ChainUnavailable:
        return SyncResult(symbol, 0, 0, 0, 0, 0, None, skipped_reason="no_chain")
    except Exception:
        logger.warning("Chain fetch failed for %s", symbol, exc_info=True)
        return SyncResult(symbol, 0, 0, 0, 0, 0, None, skipped_reason="fetch_failed")

    if not chain.quotes:
        return SyncResult(symbol, 0, 0, 0, 0, 0, None, skipped_reason="empty_chain")

    return await persist_chain(db, underlying_id, chain, dividend_yield, full=full, now=now)


async def persist_chain(
    db: AsyncSession, underlying_id: int, chain: RawChain,
    dividend_yield: float = 0.0, full: bool = False, now: datetime | None = None,
) -> SyncResult:
    """Persist an already-fetched chain. Split out so it is testable without
    touching the network — the fetch is the only impure part above."""
    now = now or datetime.now(tz=timezone.utc)
    slot = floor_to_slot(now, settings.base_poll_minutes)
    today = slot.date()

    contract_repo = ContractRepository(db)
    quote_repo = QuoteRepository(db)

    # 1. Register contracts. Every contract in the response is registered, even
    #    ones this tick will not quote — the dimension table is the catalogue
    #    of what exists, independent of sampling.
    before = await contract_repo.count_for(underlying_id)
    ids = await contract_repo.upsert_many(underlying_id, chain.quotes, seen_at=now)
    after = await contract_repo.count_for(underlying_id)

    # 2. Cadence selection. The EOD snapshot takes everything; an intraday tick
    #    takes only the buckets whose slot this is.
    if full:
        selected = [q for q in chain.quotes if q.ref.expiration >= today]
    else:
        selected = [
            q for q in chain.quotes
            if should_persist(q.ref.dte(today), slot, settings.base_poll_minutes)
        ]

    if not selected:
        return SyncResult(chain.symbol, len(chain.quotes), 0, after - before, 0, 0, slot)

    # 3. Solve IV on the survivors, in one vectorised pass.
    spot = chain.spot
    bid = np.array([q.bid if q.bid is not None else np.nan for q in selected])
    ask = np.array([q.ask if q.ask is not None else np.nan for q in selected])
    mark = mid_price(bid, ask)

    if spot is None or spot <= 0:
        # No spot means no pricing basis at all. The quotes are still worth
        # storing — they are the market's prices and are not retroactively
        # recoverable — but nothing derived from them can be computed, and
        # marking that explicitly beats writing a NULL that looks like any
        # other NULL.
        iv = np.full(len(selected), np.nan)
        iv_status = np.full(len(selected), IVStatus.NO_QUOTE, dtype=object)
        logger.warning("%s: no spot in chain response; storing quotes without IV", chain.symbol)
    else:
        strike = np.array([q.ref.strike for q in selected])
        tenor = np.array([q.ref.year_fraction(now) for q in selected])
        phi = np.array([CALL if q.ref.right.value == "call" else PUT for q in selected])
        res = implied_vol(mark, spot, strike, tenor, settings.risk_free_rate, dividend_yield, phi)
        iv, iv_status = res.iv, res.status

    def _f(value):
        """NaN -> None at the DB boundary. NaN is a float and would happily
        insert as one, producing a column where 'unknown' and 'not a number'
        are indistinguishable to every future query."""
        return None if value is None or not np.isfinite(value) else float(value)

    rows = []
    for i, q in enumerate(selected):
        cid = ids.get(q.ref.occ_symbol)
        if cid is None:
            continue
        rows.append({
            "contract_id": cid, "ts": slot,
            "bid": q.bid, "ask": q.ask, "mark": _f(mark[i]),
            "last": q.last, "last_trade_at": q.last_trade_at,
            "volume": q.volume, "spot": spot,
            "iv": _f(iv[i]), "iv_provider": q.iv_provider,
            "iv_status": str(iv_status[i]),
        })

    persisted = await quote_repo.upsert_quotes(rows)

    # 4. The underlying's own spot tick, on the same grid.
    if spot is not None and spot > 0:
        await quote_repo.upsert_underlying_quote({
            "underlying_id": underlying_id, "ts": slot, "price": spot,
            "bid": chain.bid, "ask": chain.ask,
            "previous_close": chain.previous_close, "market_state": chain.market_state,
        })

    # 5. Open interest, only on the EOD pass — it changes once a day, so
    #    writing it per tick would store the same integer ~78 times a session.
    daily_rows = 0
    if full:
        session_date = chain.session_date or today
        daily_rows = await _write_daily(
            quote_repo, selected, ids, mark, iv, spot, session_date, now
        )

    solved = int(np.isfinite(iv).sum())
    return SyncResult(
        symbol=chain.symbol, fetched=len(chain.quotes), persisted=persisted,
        new_contracts=after - before, iv_solved=solved, daily_rows=daily_rows, slot=slot,
    )


async def _write_daily(
    quote_repo: QuoteRepository, selected: list, ids: dict[str, int],
    mark: np.ndarray, iv: np.ndarray, spot: float | None,
    session_date: date, now: datetime,
) -> int:
    """Write the settled per-session row for each contract."""
    def _f(value):
        return None if value is None or not np.isfinite(value) else float(value)

    rows = []
    for i, q in enumerate(selected):
        cid = ids.get(q.ref.occ_symbol)
        if cid is None:
            continue
        rows.append({
            "contract_id": cid, "session_date": session_date,
            "open_interest": q.open_interest, "volume": q.volume,
            "close_bid": q.bid, "close_ask": q.ask, "close_mark": _f(mark[i]),
            "close_last": q.last, "close_spot": spot,
            "close_iv": _f(iv[i]), "close_iv_provider": q.iv_provider,
            "captured_at": now,
        })
    return await quote_repo.upsert_daily(rows)


async def sync_all(
    db: AsyncSession, full: bool = False, now: datetime | None = None
) -> list[SyncResult]:
    """Poll every active underlying.

    Each underlying is isolated: a failure logs, rolls back its own partial
    work, and the loop continues. One bad ticker must never cost the portfolio
    a collection cycle — the same guarantee fibenchi's ``_persist_symbol``
    provides for its nightly refresh.
    """
    underlyings = await UnderlyingRepository(db).list_active()
    if not underlyings:
        return []

    # Snapshot the scalars while the rows are live: the loop below commits and
    # may roll back, and either expires every ORM instance in the session.
    targets = [(u.id, u.symbol, float(u.dividend_yield or 0.0)) for u in underlyings]

    results = []
    for uid, symbol, div in targets:
        try:
            result = await sync_underlying(db, uid, symbol, div, full=full, now=now)
        except Exception:
            logger.warning("Chain sync failed for %s; skipping", symbol, exc_info=True)
            await db.rollback()
            continue
        results.append(result)
        try:
            repo = UnderlyingRepository(db)
            if result.slot is not None:
                await repo.mark_polled(uid, result.slot)
            if full and result.daily_rows:
                await repo.mark_eod(uid, (result.slot or datetime.now(tz=timezone.utc)).date())
        except Exception:
            logger.warning("Failed to update freshness markers for %s", symbol, exc_info=True)
            await db.rollback()
    return results
