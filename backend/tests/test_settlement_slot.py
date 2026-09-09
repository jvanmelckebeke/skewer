"""Where a settlement-only chain lands on the time grid.

Eurex publishes one observation per contract per session, struck at the close.
Stamping that with the fetch time makes a re-poll look like a second
observation, so a session that has one cross-section grows ~78 identical ones
over a day and every read that treats ``ts`` as "when this was observed" is
quietly wrong about all of them.
"""

import os
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.domain.venue import Venue
from app.services.chain_sync import _slot_for
from tests.factories import make_chain

BERLIN = ZoneInfo("Europe/Berlin")
SESSION = date(2026, 9, 8)
# Well after the session it describes, and not on the same day.
FETCHED_AT = datetime(2026, 9, 9, 14, 32, 11, tzinfo=timezone.utc)


def _settlement_chain(session_date: date | None = SESSION):
    return replace(make_chain(), venue=Venue.EUREX, session_date=session_date)


def test_a_settlement_chain_lands_at_its_session_close():
    slot = _slot_for(_settlement_chain(), FETCHED_AT, settled=True)

    assert slot == datetime.combine(SESSION, time(17, 30), tzinfo=BERLIN)


def test_re_polling_the_same_session_returns_the_same_slot():
    """The idempotence the (contract_id, ts) key needs to do its job."""
    chain = _settlement_chain()
    later = FETCHED_AT.replace(hour=16, minute=57)

    assert _slot_for(chain, FETCHED_AT, settled=True) == _slot_for(chain, later, settled=True)


def test_an_intraday_chain_still_lands_on_the_current_slot():
    slot = _slot_for(make_chain(), FETCHED_AT, settled=False)

    assert slot == datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)


def test_a_settlement_chain_with_no_session_falls_back_to_now():
    """Worth keeping the quotes; the fallback logs that ts is approximate."""
    slot = _slot_for(_settlement_chain(None), FETCHED_AT, settled=True)

    assert slot == datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)


needs_postgres = pytest.mark.skipif(
    "postgresql" not in os.getenv("SKEWER_TEST_DATABASE_URL", ""),
    reason="Upserting a chain needs PostgreSQL (ON CONFLICT); set SKEWER_TEST_DATABASE_URL",
)


@needs_postgres
async def test_polling_a_settlement_chain_twice_rewrites_one_cross_section(db):
    """The point of the whole change: two polls, one stored slot."""
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services.chain_sync import persist_chain

    underlying = await UnderlyingRepository(db).create("BMW.DE", "BMW", venue=Venue.EUREX)
    chain = _settlement_chain()

    first = await persist_chain(db, underlying.id, chain, full=True, now=FETCHED_AT, settled=True)
    second = await persist_chain(
        db, underlying.id, chain, full=True,
        now=FETCHED_AT + timedelta(minutes=35), settled=True,
    )

    assert first.slot == second.slot
    assert second.new_contracts == 0

    stored = await db.execute(
        text(
            "select count(distinct q.ts) from option_quotes q"
            " join option_contracts c on c.id = q.contract_id"
            " where c.underlying_id = :uid"
        ),
        {"uid": underlying.id},
    )
    assert stored.scalar_one() == 1


async def test_freshness_records_the_poll_not_the_slot(db, monkeypatch):
    """A settlement chain lands a day back; the collector still spoke just now.

    Reading ``last_polled_at`` off the slot would report every European name
    as a day stale the instant it was successfully polled.
    """
    from app.models import Group
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services import chain_sync

    repo = UnderlyingRepository(db)
    underlying = await repo.create("BMW.DE", "BMW", venue=Venue.EUREX)
    group = Group(name="Watchlist", is_default=True, position=0)
    group.underlyings.append(underlying)
    db.add(group)
    await db.commit()

    session_close = datetime.combine(SESSION, time(17, 30), tzinfo=BERLIN)

    async def fake_sync(db_, uid, symbol, div=0.0, **kwargs):
        return chain_sync.SyncResult(symbol, 1, 1, 0, 1, 0, session_close)

    monkeypatch.setattr(chain_sync, "sync_underlying", fake_sync)

    await chain_sync.sync_all(db, now=FETCHED_AT)

    refreshed = await repo.get_by_symbol("BMW.DE")
    assert refreshed.last_polled_at == FETCHED_AT
