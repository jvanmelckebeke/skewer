"""The collection pipeline, end to end, without a network.

Requires PostgreSQL: the persistence path uses dialect-specific ON CONFLICT
upserts, which SQLite cannot execute. Set SKEWER_TEST_DATABASE_URL to run.
"""

import os
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain.occ import Right
from app.services.chain_sync import persist_chain
from tests.factories import make_chain, make_quote

pytestmark = pytest.mark.skipif(
    "postgresql" not in os.getenv("SKEWER_TEST_DATABASE_URL", ""),
    reason="Collection tests need PostgreSQL (ON CONFLICT upserts); set SKEWER_TEST_DATABASE_URL",
)

TODAY = date(2026, 8, 21)
# 14:35 UTC lands on a 5-minute slot boundary and inside the US session.
NOW = datetime(2026, 8, 21, 14, 35, tzinfo=timezone.utc)


async def _underlying(db, symbol="TEST"):
    from app.repositories.underlying_repo import UnderlyingRepository

    return await UnderlyingRepository(db).create(symbol, f"{symbol} Inc")


async def test_full_snapshot_persists_every_contract(db):
    u = await _underlying(db)
    chain = make_chain(symbol="TEST", today=TODAY)
    result = await persist_chain(db, u.id, chain, full=True, now=NOW)

    assert result.fetched == len(chain.quotes)
    assert result.persisted == len(chain.quotes)
    assert result.new_contracts == len(chain.quotes)
    # The EOD pass is the only one that writes open interest.
    assert result.daily_rows == len(chain.quotes)


async def test_intraday_poll_applies_cadence(db):
    """Only the gamma bucket is due on a slot that is not a multiple of 3."""
    u = await _underlying(db, "CAD")
    chain = make_chain(symbol="CAD", today=TODAY, dtes=(3, 20, 60, 400))
    # 14:35 -> slot index not divisible by 3 or 6, so only the 5-minute bucket.
    result = await persist_chain(db, u.id, chain, full=False, now=NOW)

    per_expiry = len(chain.quotes) // 4
    assert result.persisted == per_expiry
    assert result.new_contracts == len(chain.quotes)  # all still catalogued
    assert result.daily_rows == 0                      # intraday writes no OI


async def test_leaps_are_never_persisted_intraday(db):
    u = await _underlying(db, "LEAP")
    chain = make_chain(symbol="LEAP", today=TODAY, dtes=(400,))
    for minutes in range(0, 60, 5):
        result = await persist_chain(db, u.id, chain, full=False, now=NOW + timedelta(minutes=minutes))
        assert result.persisted == 0


async def test_writes_are_idempotent_on_the_slot_grid(db):
    """Two polls inside the same 5-minute slot must upsert, not duplicate."""
    from sqlalchemy import func, select

    from app.models import OptionQuote

    u = await _underlying(db, "IDEM")
    chain = make_chain(symbol="IDEM", today=TODAY, dtes=(3,))
    await persist_chain(db, u.id, chain, full=False, now=NOW)
    await persist_chain(db, u.id, chain, full=False, now=NOW + timedelta(seconds=90))

    count = (await db.execute(select(func.count()).select_from(OptionQuote))).scalar_one()
    assert count == len(chain.quotes)


async def test_all_contracts_in_a_poll_share_one_timestamp(db):
    from sqlalchemy import distinct, select

    from app.models import OptionQuote

    u = await _underlying(db, "SNAP")
    chain = make_chain(symbol="SNAP", today=TODAY, dtes=(3,))
    await persist_chain(db, u.id, chain, full=False, now=NOW + timedelta(seconds=137))

    slots = (await db.execute(select(distinct(OptionQuote.ts)))).scalars().all()
    assert len(slots) == 1
    assert slots[0] == datetime(2026, 8, 21, 14, 35, tzinfo=timezone.utc)


async def test_provider_zero_quotes_store_as_null_not_zero(db):
    """The closed-market case: bid/ask absent must never become a 0.0 mark."""
    from sqlalchemy import select

    from app.models import OptionQuote

    u = await _underlying(db, "ZERO")
    chain = make_chain(symbol="ZERO", today=TODAY, dtes=(3,), strikes=(100.0,))
    chain = replace(chain, quotes=[
        make_quote("ZERO", TODAY + timedelta(days=3), Right.CALL, 100.0, bid=None, ask=None)
    ])
    await persist_chain(db, u.id, chain, full=False, now=NOW)

    row = (await db.execute(select(OptionQuote))).scalars().one()
    assert row.bid is None and row.ask is None and row.mark is None
    assert row.iv is None
    assert row.iv_status == "no_quote"


async def test_expired_contracts_are_catalogued_but_not_quoted(db):
    u = await _underlying(db, "EXP")
    chain = make_chain(symbol="EXP", today=TODAY, dtes=(3,))
    # Poll a month later: every contract has expired.
    result = await persist_chain(db, u.id, chain, full=False, now=NOW + timedelta(days=30))
    assert result.persisted == 0
    assert result.new_contracts == len(chain.quotes)


async def test_missing_spot_stores_quotes_without_iv(db):
    """Quotes are the market's own prices and are not recoverable later, so
    they are stored even when nothing derived can be computed."""
    from sqlalchemy import select

    from app.models import OptionQuote

    u = await _underlying(db, "NOSPOT")
    chain = make_chain(symbol="NOSPOT", today=TODAY, dtes=(3,), strikes=(100.0,))
    chain = replace(chain, spot=None)
    result = await persist_chain(db, u.id, chain, full=False, now=NOW)

    assert result.persisted > 0
    rows = (await db.execute(select(OptionQuote))).scalars().all()
    assert all(r.iv is None and r.spot is None for r in rows)


async def test_reads_back_an_enriched_chain(db):
    from app.services.chain_service import chain_view

    u = await _underlying(db, "READ")
    chain = make_chain(symbol="READ", today=TODAY, dtes=(3,))
    await persist_chain(db, u.id, chain, full=True, now=NOW)

    view = await chain_view(db, "READ")
    assert view["symbol"] == "READ"
    assert view["spot"] == pytest.approx(100.0)
    assert len(view["contracts"]) == len(chain.quotes)
    priced = [c for c in view["contracts"] if c["iv"] is not None]
    assert priced, "synthetic two-sided quotes should imply a volatility"
    for c in priced:
        assert c["delta"] is not None and abs(c["delta"]) <= 1.0
        assert c["open_interest"] == 100
    assert "atm_iv" in view["metrics"]
