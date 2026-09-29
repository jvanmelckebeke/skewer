"""Trading calendars, one per venue.

The calendar exists to answer one question — *should this underlying be polled
right now?* Getting that from a real exchange calendar rather than a weekday
check matters more than it sounds: polling through Thanksgiving, Good Friday or
a half-day close writes ~78 identical snapshots of a frozen chain, which is not
just waste but noise that every downstream aggregate has to be taught to ignore.

Each venue names its ``exchange_calendars`` calendar in its ``VenueSpec``, and
one calendar object per name is cached for the process, the same approach as
fibenchi's ``market_calendar.venue_for``. The calendar decides which days are
sessions and when a session closes early; the venue's own session hours bound
it. That intersection is needed because the calendar is not always the
option market's: ``XEUR`` runs 08:00-22:00 for futures, while Eurex equity
options trade 09:00-17:30.

``VenueSpec.feed_delay`` is how far behind the market the venue's intraday
feed runs. A quote that is 15 minutes late describes the book 15 minutes ago,
so "is this feed showing an open market?" is asked about ``now - feed_delay``.
That moves the whole polling window: it opens at open + delay and closes at
close + delay, so the last slots of the session are still collected after the
bell.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.domain.venue import Venue, VenueSpec, spec_for

logger = logging.getLogger(__name__)

# The timezone the session-profile queries in quote_repo date a row in. It is
# New York's for every venue, which files a Eurex row under the right date
# only because Berlin and New York share a calendar day during both sessions.
MARKET_TZ = spec_for(Venue.OCC).expiry_tz

# One calendar per name, shared by every venue that names it. None marks a
# calendar that failed to load, so the failure is logged once, not per poll.
_calendars: dict[str, Any] = {}


def _calendar(spec: VenueSpec) -> Any | None:
    """The venue's exchange calendar, or None if unavailable.

    Fail-safe rather than fail-closed: if the calendar cannot load, polling
    falls back to a weekday/hours heuristic and keeps collecting. Losing a
    holiday check costs some redundant rows; refusing to poll would cost data
    that is not recoverable after the fact.
    """
    name = spec.calendar
    if name not in _calendars:
        try:
            import exchange_calendars as xcals

            _calendars[name] = xcals.get_calendar(name)
        except Exception:
            logger.warning(
                "Calendar %s unavailable; falling back to weekday heuristics", name, exc_info=True
            )
            _calendars[name] = None
    return _calendars[name]


def local_now(now: datetime | None = None, venue: Venue = Venue.OCC) -> datetime:
    return (now or datetime.now(tz=timezone.utc)).astimezone(spec_for(venue).expiry_tz)


def is_session(day: date, venue: Venue = Venue.OCC) -> bool:
    """Whether ``day`` is a trading session on ``venue``."""
    cal = _calendar(spec_for(venue))
    if cal is None:
        return day.weekday() < 5
    try:
        return bool(cal.is_session(day))
    except Exception:
        return day.weekday() < 5


def session_bounds(day: date, venue: Venue = Venue.OCC) -> tuple[datetime, datetime] | None:
    """``(open, close)`` in venue-local time, half-day aware.

    The calendar's session clipped to the venue's own hours, so an early close
    still ends the day early and a calendar wider than the option market does
    not widen it.
    """
    spec = spec_for(venue)
    tz = spec.expiry_tz
    hours_open = datetime.combine(day, spec.session_open, tzinfo=tz)
    hours_close = datetime.combine(day, spec.session_close, tzinfo=tz)
    cal = _calendar(spec)
    if cal is None:
        if day.weekday() >= 5:
            return None
        return hours_open, hours_close
    try:
        if not cal.is_session(day):
            return None
        open_at = max(cal.session_open(day).astimezone(tz), hours_open)
        close_at = min(cal.session_close(day).astimezone(tz), hours_close)
    except Exception:
        return None
    return (open_at, close_at) if open_at < close_at else None


def _feed_now(now: datetime | None, venue: Venue) -> datetime:
    """The instant the venue's feed is showing at ``now``."""
    return local_now((now or datetime.now(tz=timezone.utc)) - spec_for(venue).feed_delay, venue)


def is_market_open(now: datetime | None = None, venue: Venue = Venue.OCC) -> bool:
    """Whether the venue's feed is showing an open session at ``now``."""
    local = _feed_now(now, venue)
    bounds = session_bounds(local.date(), venue)
    if bounds is None:
        return False
    open_at, close_at = bounds
    return open_at <= local <= close_at


def minutes_since_open(now: datetime | None = None, venue: Venue = Venue.OCC) -> float | None:
    """How long the session has been open, or None when it is not.

    Exists so "nothing has been polled recently" can be told apart from
    "nothing was due yet". One minute after the open, the last poll is always
    yesterday's, and judging that against a fixed window flags every
    underlying every morning. Measured on the feed's clock, so a delayed feed
    is not "late" during the first ``feed_delay`` minutes of the session.
    """
    local = _feed_now(now, venue)
    bounds = session_bounds(local.date(), venue)
    if bounds is None:
        return None
    open_at, close_at = bounds
    if not (open_at <= local <= close_at):
        return None
    return (local - open_at).total_seconds() / 60.0


def previous_session(day: date, venue: Venue = Venue.OCC) -> date | None:
    """The most recent session on or before ``day``.

    Used by the EOD job: it fires after the close, and on a holiday there is
    no session to snapshot rather than an earlier one to backfill.
    """
    cal = _calendar(spec_for(venue))
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
        logger.warning("Calendar lookup failed for %s on %s", day, venue, exc_info=True)
        return None
