import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Date, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OptionContract, OptionDailyStat, OptionQuote, UnderlyingQuote
from app.services.market_calendar import MARKET_TZ

logger = logging.getLogger(__name__)

# Quote rows are wider than contract rows (12 columns), so the chunk is sized
# to keep parameters per statement well under PostgreSQL's 32767.
UPSERT_CHUNK_ROWS = 1000


def _chunked(rows: list[dict], size: int) -> Iterator[list[dict]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


class QuoteRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_quotes(self, rows: list[dict]) -> int:
        """Write option quote rows, idempotently on ``(contract_id, ts)``.

        Idempotency matters more here than it looks: because timestamps are
        snapped to the poll grid, a retry or an overlapping run writes the
        *same* key rather than a near-duplicate row a millisecond apart. That
        is what keeps a stored cross-section a clean cross-section.
        """
        if not rows:
            return 0
        written = 0
        for chunk in _chunked(rows, UPSERT_CHUNK_ROWS):
            stmt = pg_insert(OptionQuote).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["contract_id", "ts"],
                set_={
                    c: stmt.excluded[c]
                    for c in ("bid", "ask", "mark", "last", "last_trade_at",
                              "volume", "spot", "iv", "iv_provider", "iv_status")
                },
            )
            await self.db.execute(stmt)
            written += len(chunk)
        await self.db.commit()
        return written

    async def upsert_underlying_quote(self, row: dict) -> None:
        stmt = pg_insert(UnderlyingQuote).values([row])
        stmt = stmt.on_conflict_do_update(
            index_elements=["underlying_id", "ts"],
            set_={c: stmt.excluded[c] for c in ("price", "bid", "ask", "previous_close", "market_state")},
        )
        await self.db.execute(stmt)
        await self.db.commit()

    async def upsert_daily(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        written = 0
        for chunk in _chunked(rows, UPSERT_CHUNK_ROWS):
            stmt = pg_insert(OptionDailyStat).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["contract_id", "session_date"],
                set_={
                    c: stmt.excluded[c]
                    for c in ("open_interest", "volume", "close_bid", "close_ask",
                              "close_mark", "close_last", "close_spot", "close_iv",
                              "close_iv_provider", "captured_at")
                },
            )
            await self.db.execute(stmt)
            written += len(chunk)
        await self.db.commit()
        return written

    async def latest_ts(self, underlying_id: int) -> datetime | None:
        """Most recent captured slot for an underlying.

        Goes through the contract table rather than scanning the hypertable
        blind, so the planner can use the contract index instead of touching
        every chunk.
        """
        result = await self.db.execute(
            select(OptionQuote.ts)
            .join(OptionContract, OptionContract.id == OptionQuote.contract_id)
            .where(OptionContract.underlying_id == underlying_id)
            .order_by(OptionQuote.ts.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def snapshot_at(
        self, underlying_id: int, ts: datetime, expiration: date | None = None
    ) -> list[tuple]:
        """Every stored quote for an underlying at exactly one slot.

        Exact-match on ``ts`` rather than a window, because the grid guarantees
        contracts captured together share a timestamp — see domain/cadence.py.
        Contracts in slower cadence buckets are simply absent from a slot they
        were not sampled in, which is the honest representation: their value at
        that instant was not observed, and interpolating one would invent data.
        """
        stmt = (
            select(
                OptionContract.id, OptionContract.occ_symbol,
                OptionQuote.bid, OptionQuote.ask, OptionQuote.mark, OptionQuote.last,
                OptionQuote.last_trade_at, OptionQuote.volume, OptionQuote.spot,
                OptionQuote.iv, OptionQuote.iv_provider, OptionQuote.iv_status,
            )
            .join(OptionQuote, OptionQuote.contract_id == OptionContract.id)
            .where(OptionContract.underlying_id == underlying_id, OptionQuote.ts == ts)
        )
        if expiration is not None:
            stmt = stmt.where(OptionContract.expiration == expiration)
        result = await self.db.execute(stmt.order_by(OptionContract.expiration, OptionContract.strike))
        return list(result.all())

    async def latest_open_interest(self, underlying_id: int) -> dict[int, int]:
        """Most recent open interest per contract, from the daily table.

        OI lives only in the daily table (it changes once a day), so an
        intraday chain view has to join it in from there rather than expecting
        it on the tick.
        """
        sub = (
            select(
                OptionDailyStat.contract_id,
                OptionDailyStat.open_interest,
                OptionDailyStat.session_date,
            )
            .join(OptionContract, OptionContract.id == OptionDailyStat.contract_id)
            .where(OptionContract.underlying_id == underlying_id)
            .order_by(OptionDailyStat.contract_id, OptionDailyStat.session_date.desc())
            .distinct(OptionDailyStat.contract_id)
            .subquery()
        )
        result = await self.db.execute(select(sub.c.contract_id, sub.c.open_interest))
        return {cid: oi for cid, oi in result.all() if oi is not None}

    async def series_for_contract(
        self, contract_id: int, start: datetime, end: datetime
    ) -> list[tuple]:
        """One contract's quote history — the per-contract time-series read."""
        result = await self.db.execute(
            select(OptionQuote.ts, OptionQuote.mark, OptionQuote.bid, OptionQuote.ask,
                   OptionQuote.iv, OptionQuote.spot, OptionQuote.volume)
            .where(
                OptionQuote.contract_id == contract_id,
                OptionQuote.ts >= start,
                OptionQuote.ts <= end,
            )
            .order_by(OptionQuote.ts)
        )
        return list(result.all())

    # ---- session profiles ------------------------------------------------
    #
    # The reads above answer "what did the chain look like at one instant" and
    # "what did one contract do over time". These answer the third question:
    # what did every strike in one expiration do over one *session*. That is a
    # distribution per contract rather than a value per contract, which is why
    # it is an aggregate query rather than a fetch-and-reduce in Python — 80
    # slots times a few thousand contracts is not a payload worth moving.

    async def available_sessions(
        self, underlying_id: int, lookback_days: int = 180
    ) -> list[tuple[date, int]]:
        """Session dates that hold option quotes, with slot counts, newest first.

        The count travels with the date because it is the difference between a
        session worth reading and one that cannot answer the question. A date
        alone in a picker looks equally useful whether it holds 80 captured
        slots or the single one a weekend catalogue refresh leaves behind.

        Sourced from ``option_quotes`` rather than the much smaller
        ``underlying_quotes``, because the two genuinely disagree: the spot tick
        is written on every poll, while option rows go through cadence
        selection first. A weekend catalogue refresh therefore leaves a spot
        slot and no quotes at all, and listing that as a session would offer a
        date that can only 404.

        The lookback bound is what keeps this cheap. An unbounded DISTINCT over
        a hypertable touches every chunk ever written; with a time predicate
        Timescale excludes all but the recent ones, and a session picker has no
        use for a date from three years ago anyway.
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
        local_date = cast(func.timezone(str(MARKET_TZ), OptionQuote.ts), Date)
        result = await self.db.execute(
            select(
                local_date.label("session_date"),
                func.count(func.distinct(OptionQuote.ts)).label("slots"),
            )
            .join(OptionContract, OptionContract.id == OptionQuote.contract_id)
            .where(
                OptionContract.underlying_id == underlying_id,
                OptionQuote.ts >= cutoff,
            )
            .group_by(local_date)
            .order_by(local_date.desc())
        )
        return [(row.session_date, int(row.slots)) for row in result.all()]

    async def session_expirations(self, underlying_id: int, session_date: date) -> list[date]:
        """Expirations that hold quotes in one session, nearest first.

        A session view cannot borrow the live chain's expiration list: a
        session from three weeks ago has expiries that have since expired off
        it, and those are exactly the ones with the densest history. So the
        list comes from the session itself.
        """
        local_date = cast(func.timezone(str(MARKET_TZ), OptionQuote.ts), Date)
        result = await self.db.execute(
            select(OptionContract.expiration)
            .join(OptionQuote, OptionQuote.contract_id == OptionContract.id)
            .where(
                OptionContract.underlying_id == underlying_id,
                local_date == session_date,
            )
            .group_by(OptionContract.expiration)
            .order_by(OptionContract.expiration)
        )
        return list(result.scalars().all())

    async def session_profile(
        self, underlying_id: int, session_date: date, expiration: date
    ) -> list[tuple]:
        """Per-contract quantiles over every slot captured in one session.

        Quantiles rather than min/max alone because the extremes of an intraday
        IV series are usually one bad print; the interquartile range is what
        actually says how settled a strike was. ``percentile_cont`` ignores
        NULLs, so an unpriced slot lowers ``priced`` without dragging a
        quantile toward zero — the same rule as everywhere else here, that a
        missing number is missing rather than nought.

        PostgreSQL-only: ordered-set aggregates and ``timezone()`` have no
        SQLite equivalent.
        """
        local_date = cast(func.timezone(str(MARKET_TZ), OptionQuote.ts), Date)
        # Relative to mark, because a 0.05 spread is nothing on a 9.00 contract
        # and half the value of a 0.10 one. Absolute width is not comparable
        # across a chain; this is.
        relative_spread = (OptionQuote.ask - OptionQuote.bid) / func.nullif(OptionQuote.mark, 0)

        def pct(column, fraction: float):
            return func.percentile_cont(fraction).within_group(column.asc())

        result = await self.db.execute(
            select(
                OptionContract.occ_symbol,
                OptionContract.strike,
                OptionContract.right,
                func.count().label("samples"),
                func.count(OptionQuote.iv).label("priced"),
                pct(OptionQuote.iv, 0.25).label("iv_p25"),
                pct(OptionQuote.iv, 0.50).label("iv_p50"),
                pct(OptionQuote.iv, 0.75).label("iv_p75"),
                func.min(OptionQuote.iv).label("iv_min"),
                func.max(OptionQuote.iv).label("iv_max"),
                pct(OptionQuote.mark, 0.50).label("mark_p50"),
                pct(relative_spread, 0.50).label("spread_p50"),
                func.max(OptionQuote.volume).label("volume"),
            )
            .join(OptionQuote, OptionQuote.contract_id == OptionContract.id)
            .where(
                OptionContract.underlying_id == underlying_id,
                OptionContract.expiration == expiration,
                local_date == session_date,
            )
            .group_by(OptionContract.id, OptionContract.occ_symbol,
                      OptionContract.strike, OptionContract.right)
            .order_by(OptionContract.strike, OptionContract.right)
        )
        return list(result.all())

    async def session_spot(self, underlying_id: int, session_date: date) -> tuple | None:
        """Low and high spot across one session.

        A range, not a point: over a session the underlying moves, and a
        session-profile chart that marks "the money" at a single strike would
        put it somewhere the stock spent most of the day away from.
        """
        local_date = cast(func.timezone(str(MARKET_TZ), UnderlyingQuote.ts), Date)
        result = await self.db.execute(
            select(
                func.min(UnderlyingQuote.price).label("low"),
                func.max(UnderlyingQuote.price).label("high"),
                func.count().label("slots"),
            )
            .where(
                UnderlyingQuote.underlying_id == underlying_id,
                local_date == session_date,
            )
        )
        row = result.one_or_none()
        return row if row and row.slots else None
