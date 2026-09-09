"""When collection counts as stalled.

The interesting case is the first minutes of a session, where the most recent
poll is always the previous day's and a naive window flags everything.
"""

from datetime import datetime, timedelta, timezone

from app.models import Underlying
from app.services.underlying_service import STALE_AFTER_MINUTES, is_stale


def _underlying(minutes_ago: float | None, is_active: bool = True) -> Underlying:
    last = (
        None if minutes_ago is None
        else datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_ago)
    )
    return Underlying(symbol="TEST", name="Test", last_polled_at=last, is_active=is_active)


def test_nothing_is_stale_while_the_market_is_closed():
    """Out of session no poll is due, so a 16-hour gap is correct behaviour."""
    assert is_stale(_underlying(minutes_ago=16 * 60), open_for=None) is False


def test_nothing_is_stale_just_after_the_open():
    """The regression. At 09:31 the last poll is yesterday's, for everything."""
    assert is_stale(_underlying(minutes_ago=16 * 60), open_for=1.0) is False


def test_nothing_is_stale_exactly_at_the_threshold():
    assert is_stale(_underlying(minutes_ago=16 * 60), open_for=STALE_AFTER_MINUTES) is False


def test_a_missed_poll_mid_session_is_stale():
    assert is_stale(_underlying(minutes_ago=40), open_for=120.0) is True


def test_a_recent_poll_mid_session_is_not_stale():
    assert is_stale(_underlying(minutes_ago=3), open_for=120.0) is False


def test_never_polled_well_into_the_session_is_stale():
    assert is_stale(_underlying(minutes_ago=None), open_for=120.0) is True


def test_a_paused_underlying_is_never_stale():
    """Paused is a state you chose. It is reported as paused, not as broken."""
    assert is_stale(_underlying(minutes_ago=None, is_active=False), open_for=300.0) is False
