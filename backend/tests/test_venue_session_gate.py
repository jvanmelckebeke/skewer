"""Each venue is polled in its own session, on its feed's clock.

NVDA and BMW.DE share a polling cycle and nothing else: different holidays,
different hours, and (once the IBKR feed arrives) a Eurex feed that runs
fifteen minutes behind the market.
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.domain import venue as venue_mod
from app.domain.venue import Venue
from app.services import chain_sync
from app.services.chain_sync import SyncResult, _venue_due, sync_all
from app.services.market_calendar import (
    is_market_open,
    minutes_since_open,
    session_bounds,
)

BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")

THANKSGIVING = date(2026, 11, 26)  # NYSE shut, Eurex open
BLACK_FRIDAY = date(2026, 11, 27)  # NYSE half day
CHRISTMAS_EVE = date(2026, 12, 24)  # Eurex shut, NYSE open


def _at(day: date, clock: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, clock, tzinfo=tz).astimezone(timezone.utc)


@pytest.fixture
def delayed_eurex(monkeypatch):
    """Eurex served by a feed 15 minutes behind the market."""
    spec = replace(venue_mod.VENUES[Venue.EUREX], feed_delay=timedelta(minutes=15))
    monkeypatch.setitem(venue_mod.VENUES, Venue.EUREX, spec)


# ------------------------------------------------------------ session hours


def test_eurex_hours_are_the_option_markets_not_the_futures_calendars():
    """XEUR runs 08:00-22:00 for futures; equity options trade 09:00-17:30."""
    open_at, close_at = session_bounds(date(2026, 9, 29), Venue.EUREX)

    assert open_at.astimezone(BERLIN).time() == time(9, 0)
    assert close_at.astimezone(BERLIN).time() == time(17, 30)


def test_a_new_york_half_day_still_closes_early():
    _open_at, close_at = session_bounds(BLACK_FRIDAY, Venue.OCC)

    assert close_at.astimezone(NEW_YORK).time() == time(13, 0)


def test_holidays_are_per_venue():
    noon_berlin = _at(THANKSGIVING, time(12, 0), BERLIN)
    assert is_market_open(noon_berlin, Venue.EUREX)
    assert not is_market_open(_at(THANKSGIVING, time(12, 0), NEW_YORK), Venue.OCC)

    assert not is_market_open(_at(CHRISTMAS_EVE, time(12, 0), BERLIN), Venue.EUREX)
    assert is_market_open(_at(CHRISTMAS_EVE, time(12, 0), NEW_YORK), Venue.OCC)


# ------------------------------------------------------------- feed delay


def test_a_delayed_feed_opens_and_closes_late(delayed_eurex):
    day = date(2026, 9, 29)

    assert not is_market_open(_at(day, time(9, 10), BERLIN), Venue.EUREX)
    assert is_market_open(_at(day, time(9, 15), BERLIN), Venue.EUREX)
    # After the bell the feed is still showing the last minutes of the session.
    assert is_market_open(_at(day, time(17, 40), BERLIN), Venue.EUREX)
    assert not is_market_open(_at(day, time(17, 50), BERLIN), Venue.EUREX)


def test_a_delayed_feed_is_not_late_at_its_own_open(delayed_eurex):
    day = date(2026, 9, 29)

    assert minutes_since_open(_at(day, time(9, 20), BERLIN), Venue.EUREX) == pytest.approx(5.0)


def test_a_venue_without_a_delay_is_unchanged():
    day = date(2026, 9, 29)

    assert is_market_open(_at(day, time(9, 30), NEW_YORK), Venue.OCC)
    assert not is_market_open(_at(day, time(16, 5), NEW_YORK), Venue.OCC)


# ------------------------------------------------------------- the poll gate


def test_intraday_due_follows_each_venues_session():
    afternoon_berlin = _at(date(2026, 9, 29), time(12, 0), BERLIN)  # 06:00 in New York

    assert _venue_due(Venue.EUREX, full=False, now=afternoon_berlin)
    assert not _venue_due(Venue.OCC, full=False, now=afternoon_berlin)


def test_eod_is_due_only_where_there_was_a_session_today():
    evening = datetime.combine(THANKSGIVING, time(21, 10), tzinfo=timezone.utc)

    assert _venue_due(Venue.EUREX, full=True, now=evening)
    assert not _venue_due(Venue.OCC, full=True, now=evening)


async def test_sync_all_skips_underlyings_whose_venue_is_shut(db, monkeypatch):
    from app.models import Group
    from app.models.group import group_underlyings
    from app.repositories.underlying_repo import UnderlyingRepository

    group = Group(name="Watchlist", is_default=True, position=0)
    db.add(group)
    await db.commit()
    repo = UnderlyingRepository(db)
    for symbol, venue in (("NVDA", Venue.OCC), ("BMW.DE", Venue.EUREX)):
        row = await repo.create(symbol, symbol, venue=venue)
        await db.execute(group_underlyings.insert().values(group_id=group.id, underlying_id=row.id))
    await db.commit()

    polled: list[str] = []

    async def fake_sync(db, uid, symbol, *args, **kwargs):
        polled.append(symbol)
        return SyncResult(symbol, 0, 0, 0, 0, 0, None)

    monkeypatch.setattr(chain_sync, "sync_underlying", fake_sync)
    afternoon_berlin = _at(date(2026, 9, 29), time(12, 0), BERLIN)

    await sync_all(db, full=False, now=afternoon_berlin, only_due=True)
    assert polled == ["BMW.DE"]

    polled.clear()
    await sync_all(db, full=False, now=afternoon_berlin)
    assert sorted(polled) == ["BMW.DE", "NVDA"]
