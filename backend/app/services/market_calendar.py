"""US equity-options trading calendar.

Deliberately one small module rather than fibenchi's multi-venue package: you
accepted US-only for now, and Yahoo publishes option chains for US listings
only anyway, so a venue-resolution layer would be scaffolding with nothing to
resolve. When a European source arrives this is the seam that grows.

The calendar exists to answer one question — *should the poller run right now?*
Getting that from a real exchange calendar rather than a weekday check matters
more than it sounds: polling through Thanksgiving, Good Friday or a half-day
close writes ~78 identical snapshots of a frozen chain, which is not just waste
but noise that every downstream aggregate has to be taught to ignore.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("America/New_York")
CALENDAR_NAME = "XNYS"

# Regular session. Half days close at 13:00 and the calendar knows which ones,
# so these are only the fallback when exchange_calendars is unavailable.
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


@lru_cache(maxsize=1)
def _calendar():
    """The exchange calendar, or None if unavailable.

    Fail-safe rather than fail-closed: if the calendar cannot load, polling
    falls back to a weekday/hours heuristic and keeps collecting. Losing a
    holiday check costs some redundant rows; refusing to poll would cost data
    that is not recoverable after the fact.
    """
    try:
        import exchange_calendars as xcals

        return xcals.get_calendar(CALENDAR_NAME)
    except Exception:
        logger.warning("exchange_calendars unavailable; falling back to weekday heuristics", exc_info=True)
        return None


def local_now(now: datetime | None = None) -> datetime:
    from datetime import timezone

    return (now or datetime.now(tz=timezone.utc)).astimezone(MARKET_TZ)


def is_session(day: date) -> bool:
    """Whether ``day`` is a trading session."""
    cal = _calendar()
    if cal is None:
        return day.weekday() < 5
    try:
        return bool(cal.is_session(day))
    except Exception:
        return day.weekday() < 5


def session_bounds(day: date) -> tuple[datetime, datetime] | None:
    """``(open, close)`` in market-local time, half-day aware."""
    cal = _calendar()
    if cal is None:
        if day.weekday() >= 5:
            return None
        return (
            datetime.combine(day, REGULAR_OPEN, tzinfo=MARKET_TZ),
            datetime.combine(day, REGULAR_CLOSE, tzinfo=MARKET_TZ),
        )
    try:
        if not cal.is_session(day):
            return None
        return (
            cal.session_open(day).astimezone(MARKET_TZ),
            cal.session_close(day).astimezone(MARKET_TZ),
        )
    except Exception:
        return None


def is_market_open(now: datetime | None = None) -> bool:
    """Whether the regular US equity session is open right now."""
    local = local_now(now)
    bounds = session_bounds(local.date())
    if bounds is None:
        return False
    open_at, close_at = bounds
    return open_at <= local <= close_at


def previous_session(day: date) -> date | None:
    """The most recent session on or before ``day``.

    Used by the EOD job: it fires after the close, and on a holiday there is
    no session to snapshot rather than an earlier one to backfill.
    """
    cal = _calendar()
    if cal is None:
        cursor = day
        for _ in range(10):
            if cursor.weekday() < 5:
                return cursor
            cursor -= timedelta(days=1)
        return None
    try:
        if cal.is_session(day):
            return day
        # `previous_session` requires its argument to *be* a session and raises
        # NotSessionError otherwise — which is exactly the case we are asking
        # about. `date_to_session` is the roll-backwards lookup.
        return cal.date_to_session(day, direction="previous").date()
    except Exception:
        logger.warning("Calendar lookup failed for %s", day, exc_info=True)
        return None
