"""Read-side: reconstruct an enriched chain from stored rows."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import numpy as np
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.occ import ContractRef
from app.repositories.group_repo import GroupRepository
from app.repositories.quote_repo import QuoteRepository
from app.repositories.underlying_repo import UnderlyingRepository
from app.services.compute.analytics import METRIC_REGISTRY, compute_all, smile, term_structure
from app.services.compute.bs import per_day, per_point
from app.services.compute.chain import ChainSnapshot, enrich

logger = logging.getLogger(__name__)


async def get_underlying_or_404(db: AsyncSession, symbol: str):
    row = await UnderlyingRepository(db).get_by_symbol(symbol)
    if row is None:
        raise HTTPException(404, f"Underlying {symbol.upper()!r} is not tracked")
    return row


async def load_snapshot(
    db: AsyncSession, symbol: str, at: datetime | None = None,
    expiration: date | None = None,
) -> tuple[ChainSnapshot, object, list[tuple], datetime]:
    """Build a :class:`ChainSnapshot` from the stored slot at (or before) ``at``.

    Uses the persisted IV rather than re-solving: the collector already did
    that work with the live two-sided quote in hand, and re-solving from a
    stored mark would silently produce a *different* number wherever the
    contract has since moved. One IV per stored row, decided once.
    """
    underlying = await get_underlying_or_404(db, symbol)
    return await load_snapshot_for(db, underlying, at=at, expiration=expiration)


async def load_snapshot_for(
    db: AsyncSession, underlying, at: datetime | None = None,
    expiration: date | None = None,
) -> tuple[ChainSnapshot, object, list[tuple], datetime]:
    """:func:`load_snapshot` for a row already in hand.

    The summary endpoint walks every tracked underlying, and looking each one
    up again by symbol when the ORM row is already loaded is a query per row
    for nothing.
    """
    repo = QuoteRepository(db)

    ts = at or await repo.latest_ts(underlying.id)
    if ts is None:
        raise HTTPException(404, f"No option data collected yet for {underlying.symbol}")

    rows = await repo.snapshot_at(underlying.id, ts, expiration=expiration)
    if not rows:
        raise HTTPException(404, f"No stored chain for {underlying.symbol} at {ts.isoformat()}")

    oi_map = await repo.latest_open_interest(underlying.id)

    refs, bid, ask, iv, volume, oi, spots = [], [], [], [], [], [], []
    for (cid, sym, venue, expiration, strike, right, exercise_style,
         contract_size, currency,
         b, a, _mark, _last, _lta, vol, spot, iv_v, _ivp, _st) in rows:
        refs.append(ContractRef(
            symbol=sym, venue=venue, root=sym.split(":")[-1],
            expiration=expiration, right=right, strike=float(strike),
            exercise_style=exercise_style, contract_size=int(contract_size),
            currency=currency, id=cid,
        ))
        bid.append(np.nan if b is None else float(b))
        ask.append(np.nan if a is None else float(a))
        iv.append(np.nan if iv_v is None else float(iv_v))
        volume.append(float(vol or 0))
        oi.append(float(oi_map.get(cid, np.nan)))
        if spot is not None:
            spots.append(float(spot))

    # Spot is denormalised onto every quote row, so any row carries it; the
    # median guards against a single malformed row skewing the whole snapshot.
    spot_value = float(np.median(spots)) if spots else 0.0

    snap = enrich(
        refs=refs, now=ts, spot=spot_value,
        bid=np.array(bid), ask=np.array(ask),
        stored_iv=np.array(iv), volume=np.array(volume), open_interest=np.array(oi),
        dividend_yield=float(underlying.dividend_yield or 0.0),
    )
    # Rows and ts travel back with the snapshot so callers never re-query for
    # the raw columns the snapshot does not carry (last, last_trade_at,
    # iv_status). Re-querying would also risk reading a *different* slot if a
    # poll landed in between.
    return snap, underlying, rows, ts


def _f(value) -> float | None:
    """Array scalar -> JSON-safe float, NaN -> None.

    Pydantic would serialise a NaN float as literal ``NaN``, which is not valid
    JSON and which most clients parse as a crash rather than a null.
    """
    if value is None:
        return None
    v = float(value)
    return None if not np.isfinite(v) else v


def to_contract_quotes(snap: ChainSnapshot, rows: list[tuple], on: date) -> list[dict]:
    """Serialise an enriched snapshot into API shape."""
    vega_pt = per_point(snap.vega)
    theta_day = per_day(snap.theta)
    moneyness = snap.moneyness

    out = []
    for i, ref in enumerate(snap.refs):
        (_cid, _sym, _venue, _exp, _strike, _right, _style, _size, _ccy,
         b, a, mark, last, last_trade_at, vol, _spot, _iv, ivp, status) = rows[i]
        out.append({
            "occ_symbol": ref.occ_symbol,
            "expiration": ref.expiration,
            "strike": ref.strike,
            "right": ref.right.value,
            "dte": ref.dte(on),
            "bid": _f(b), "ask": _f(a), "mark": _f(mark), "last": _f(last),
            "last_trade_at": last_trade_at,
            "volume": int(vol or 0),
            "open_interest": None if not np.isfinite(snap.open_interest[i]) else int(snap.open_interest[i]),
            "iv": _f(snap.iv[i]),
            "iv_provider": _f(ivp),
            "iv_status": status or "ok",
            "delta": _f(snap.delta[i]), "gamma": _f(snap.gamma[i]),
            "vega": _f(vega_pt[i]), "theta": _f(theta_day[i]), "rho": _f(snap.rho[i]),
            "model_price": _f(snap.model_price[i]),
            "moneyness": _f(moneyness[i]),
        })
    return out


async def chain_view(
    db: AsyncSession, symbol: str, at: datetime | None = None,
    expiration: date | None = None, with_metrics: bool = True,
) -> dict:
    snap, underlying, rows, ts = await load_snapshot(db, symbol, at=at, expiration=expiration)
    contracts = to_contract_quotes(snap, rows, on=ts.date())

    return {
        "symbol": underlying.symbol,
        "ts": ts,
        "spot": _f(snap.spot),
        "expirations": sorted({r.expiration for r in snap.refs}),
        "contracts": contracts,
        # Metrics are computed on the *filtered* snapshot when an expiration is
        # requested, which is deliberate: "ATM IV" for one expiry is a
        # different and more useful number than the front-month one.
        "metrics": compute_all(snap) if with_metrics else {},
    }


async def chain_summary(db: AsyncSession, group_id: int | None = None) -> list[dict]:
    """One row per tracked underlying: spot plus every registered metric.

    Built for the overview, which needs the same numbers for N underlyings and
    would otherwise pull N full chains over the wire to compute them client
    side. The metric set is read from ``METRIC_REGISTRY``, so a metric added
    with ``@chain_metric`` shows up here for free, exactly as it does on the
    chain page.

    An underlying that has never been polled is a row of nulls rather than an
    omission or an error. Leaving it out would make a freshly added ticker
    look untracked.
    """
    if group_id is None:
        underlyings = await UnderlyingRepository(db).list_all()
    else:
        group = await GroupRepository(db).get(group_id)
        if group is None:
            raise HTTPException(404, f"No group with id {group_id}")
        underlyings = list(group.underlyings)

    rows = []
    for underlying in underlyings:
        row = {
            "symbol": underlying.symbol,
            "name": underlying.name,
            "venue": underlying.venue,
            "currency": underlying.currency,
            "is_active": underlying.is_active,
            "ts": None,
            "spot": None,
            "metrics": {mid: None for mid in METRIC_REGISTRY},
        }
        try:
            snap, _u, _raw, ts = await load_snapshot_for(db, underlying)
        except HTTPException:
            # 404 from the loader means "nothing stored yet", which is the
            # normal state for a new ticker and not worth failing the list for.
            rows.append(row)
            continue

        spot = _f(snap.spot)
        row["ts"] = ts
        # load_snapshot falls back to 0.0 when no row carried a spot. Zero is
        # never a real price, so it travels as null rather than as a number
        # that would format like one.
        row["spot"] = spot if spot else None
        row["metrics"] = {mid: _f(value) for mid, value in compute_all(snap).items()}
        rows.append(row)

    return rows


async def smile_view(db: AsyncSession, symbol: str, expiration: date, at: datetime | None = None) -> dict:
    snap, underlying, _rows, _ts = await load_snapshot(db, symbol, at=at)
    data = smile(snap, expiration)
    if not data["iv"]:
        raise HTTPException(404, f"No priced contracts for {underlying.symbol} expiring {expiration}")
    return {
        "symbol": underlying.symbol, "ts": snap.ts, "expiration": expiration,
        "spot": _f(snap.spot), **data,
    }


async def term_view(db: AsyncSession, symbol: str, at: datetime | None = None) -> dict:
    snap, underlying, _rows, _ts = await load_snapshot(db, symbol, at=at)
    return {
        "symbol": underlying.symbol, "ts": snap.ts, "spot": _f(snap.spot),
        "points": term_structure(snap),
    }


async def contract_series(
    db: AsyncSession, symbol: str, days: int = 5
) -> dict:
    """One contract's stored quote history.

    Looks the contract up by its stored symbol rather than by parsing it. The
    previous version called ``ContractRef.parse``, which is OCC-only and so
    404'd every Eurex contract before it ever reached the database — the terms
    are read back from the row instead, which works for both venues.
    """
    from sqlalchemy import select

    from app.models import OptionContract

    key = symbol.strip()
    result = await db.execute(
        select(
            OptionContract.id,
            OptionContract.symbol,
            OptionContract.expiration,
            OptionContract.strike,
            OptionContract.right,
        ).where(OptionContract.symbol == key)
    )
    row = result.first()
    if row is None:
        # Tolerate an un-normalised OCC symbol (lowercase, whitespace), which
        # is how the old parse-first path forgave sloppy input.
        result = await db.execute(
            select(
                OptionContract.id,
                OptionContract.symbol,
                OptionContract.expiration,
                OptionContract.strike,
                OptionContract.right,
            ).where(OptionContract.symbol == key.upper())
        )
        row = result.first()
    if row is None:
        raise HTTPException(404, f"Contract {key} is not tracked")
    cid, stored_symbol, expiration, strike, right = row

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days)
    rows = await QuoteRepository(db).series_for_contract(cid, start, end)
    return {
        # Wire name kept as occ_symbol for frontend compatibility even though
        # it now carries Eurex keys too. Renaming it is a frontend change as
        # well as a backend one, so it is deliberately not bundled here.
        "occ_symbol": stored_symbol,
        "expiration": expiration,
        "strike": float(strike),
        "right": right.value,
        "points": [
            {"ts": ts, "mark": _f(mark), "bid": _f(b), "ask": _f(a),
             "iv": _f(iv), "spot": _f(spot), "volume": int(vol or 0)}
            for ts, mark, b, a, iv, spot, vol in rows
        ],
    }


async def session_profile(
    db: AsyncSession, symbol: str, expiration: date | None = None, session: date | None = None
) -> dict:
    """How every strike in one expiration behaved across one session.

    The third read shape, alongside the instantaneous chain and the
    single-contract series: a distribution per strike rather than a value per
    strike. It is what turns the smile from a photograph into a range — the
    median says where vol sat, the interquartile spread says how settled it
    was, and a strike whose IQR is wide was being repriced all day.

    The spot reference is returned as a low/high pair rather than a number,
    because over a session the underlying is a range and drawing it as a line
    would put the money in one place it spent most of the day away from.
    """
    underlying = await get_underlying_or_404(db, symbol)
    repo = QuoteRepository(db)

    sessions = await repo.available_sessions(underlying.id)
    if not sessions:
        raise HTTPException(404, f"No option data collected yet for {underlying.symbol}")

    known = {day for day, _slots in sessions}
    # Default to the most recent session that can actually answer the question.
    # A one-slot session is a real session with real quotes, and it stays in the
    # list, but its quantiles all collapse onto the single observation — landing
    # there by default would show an empty chart on a page that has good data
    # one entry down.
    on = session or next(
        (day for day, count in sessions if count > 1),
        sessions[0][0],
    )
    if on not in known:
        raise HTTPException(
            404, f"No stored session for {underlying.symbol} on {on.isoformat()}"
        )

    # The expiration list has to come from the session, and the session has to
    # be resolved first, so the default expiry is resolved here rather than
    # being a parameter default: nearest-dated expiry that this session holds.
    expirations = await repo.session_expirations(underlying.id, on)
    if not expirations:
        raise HTTPException(404, f"No stored quotes for {underlying.symbol} on {on.isoformat()}")

    # Default past the same-day expiry. A 0DTE contract's implied volatility
    # goes vertical into the close as time value vanishes, so it makes a
    # spectacular but unrepresentative landing view; it stays in the list for
    # anyone who wants it.
    target = expiration or next((e for e in expirations if e > on), expirations[0])

    rows = await repo.session_profile(underlying.id, on, target)
    if not rows:
        raise HTTPException(
            404,
            f"{underlying.symbol} has no stored quotes for {target.isoformat()} on "
            f"{on.isoformat()} — the expiration may sit in a cadence bucket that is only "
            f"sampled once a day.",
        )

    spot = await repo.session_spot(underlying.id, on)

    # The slot count that matters here is how many times *this expiration* was
    # captured, not how many spot ticks the session holds — the two differ by
    # exactly the cadence bucket this expiry falls in.
    slots = max((int(r[3]) for r in rows), default=0)

    return {
        "symbol": underlying.symbol,
        "session_date": on,
        "expiration": target,
        "slots": slots,
        "spot_low": _f(spot.low) if spot else None,
        "spot_high": _f(spot.high) if spot else None,
        "available_sessions": [
            {"session_date": day, "slots": count} for day, count in sessions
        ],
        "available_expirations": expirations,
        "points": [
            {
                "occ_symbol": occ,
                "strike": float(strike),
                "right": right.value if hasattr(right, "value") else str(right),
                "samples": int(samples),
                "priced": int(priced),
                "iv_p25": _f(iv_p25), "iv_p50": _f(iv_p50), "iv_p75": _f(iv_p75),
                "iv_min": _f(iv_min), "iv_max": _f(iv_max),
                "mark_p50": _f(mark_p50),
                "spread_p50": _f(spread_p50),
                "volume": int(volume or 0),
            }
            for (occ, strike, right, samples, priced, iv_p25, iv_p50, iv_p75,
                 iv_min, iv_max, mark_p50, spread_p50, volume) in rows
        ],
    }
