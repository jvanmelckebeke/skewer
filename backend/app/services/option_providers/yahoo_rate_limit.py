"""Rate limiter + circuit breaker for Yahoo Finance calls.

A copy of fibenchi's ``services/yahoo/rate_limit.py``; a fix to one belongs
in both. Yahoo's anti-bot detection treats bursts from one IP as scraping and
answers with "Invalid Crumb" errors that block the IP for a while. A chain
poll is one options request per underlying, back to back, so without spacing
it is exactly that burst. The throttle is per process: fibenchi polls from the
same IP with its own instance, and neither sees the other's calls.

This module holds the building blocks. The orchestration (retry, fallback)
lives in :meth:`~app.services.option_providers.yahoo.YahooOptionProvider._call`,
so each Yahoo operation only wires its own ``fetch`` and ``fallback``.

Building blocks:

- :class:`YahooThrottle` — process-wide spacing + circuit breaker
- :data:`yahoo_throttle` — singleton instance used by ``YahooOptionProvider``
- :func:`crumb_rejected` — predicate on a Yahoo response dict
- :func:`check_crumb` — raises :class:`CrumbRejected` when the predicate fires
- :class:`CrumbRejected` — signal used inside fetch closures
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# Minimum spacing between Yahoo batch calls (seconds).
MIN_INTERVAL = 1.0

# Backoff schedule when "Invalid Crumb" is detected. After the last entry
# the value is held until a successful call resets the counter.
BACKOFF_SCHEDULE = (60.0, 300.0, 900.0)  # 1m, 5m, 15m


class YahooThrottle:
    """Process-wide throttle + circuit breaker. Thread-safe."""

    def __init__(self, min_interval: float = MIN_INTERVAL,
                 backoff_schedule: tuple[float, ...] = BACKOFF_SCHEDULE):
        self._lock = threading.Lock()
        self._last_call_at = 0.0
        self._blocked_until = 0.0
        self._consecutive_failures = 0
        self._min_interval = min_interval
        self._backoff_schedule = backoff_schedule

    def is_blocked(self) -> bool:
        """Return True if the circuit breaker is currently tripped."""
        with self._lock:
            return time.monotonic() < self._blocked_until

    def time_until_unblocked(self) -> float:
        """Seconds until the circuit breaker reopens, or 0 if not tripped."""
        with self._lock:
            return max(0.0, self._blocked_until - time.monotonic())

    def wait(self) -> None:
        """Block until the next Yahoo call is allowed.

        Spaces consecutive calls by at least ``min_interval`` and respects
        any active circuit-breaker window. Safe under concurrent threads;
        each caller will yield ``min_interval`` after the previous returner.
        """
        while True:
            with self._lock:
                now = time.monotonic()
                if now < self._blocked_until:
                    sleep_for = self._blocked_until - now
                else:
                    elapsed = now - self._last_call_at
                    if elapsed >= self._min_interval:
                        self._last_call_at = now
                        return
                    sleep_for = self._min_interval - elapsed
            time.sleep(sleep_for)

    def record_success(self) -> None:
        """Reset the failure counter — the call worked end-to-end."""
        with self._lock:
            if self._consecutive_failures:
                logger.info("Yahoo recovery — clearing failure counter")
            self._consecutive_failures = 0
            self._blocked_until = 0.0

    def record_invalid_crumb(self) -> None:
        """Trip the circuit breaker for the next backoff window."""
        with self._lock:
            idx = min(self._consecutive_failures, len(self._backoff_schedule) - 1)
            backoff = self._backoff_schedule[idx]
            self._consecutive_failures += 1
            self._blocked_until = time.monotonic() + backoff
            logger.warning(
                "Yahoo Invalid Crumb — pausing all Yahoo calls for %.0fs (failure #%d)",
                backoff, self._consecutive_failures,
            )

    def reset(self) -> None:
        """Reset all state. Used by tests."""
        with self._lock:
            self._last_call_at = 0.0
            self._blocked_until = 0.0
            self._consecutive_failures = 0


# Process-wide singleton — every Yahoo wrapper uses this instance.
yahoo_throttle = YahooThrottle()


def crumb_rejected(price_data: dict | None, threshold: float = 0.5) -> bool:
    """Return True if more than ``threshold`` of the response symbols were
    rejected with an "Invalid Crumb" error.

    Yahoo can legitimately return string errors for individual unknown
    tickers, so we only trip the circuit breaker when the majority of
    responses are crumb rejections — that's the signature of an IP-level
    block, not just bad symbols.
    """
    if not price_data:
        return False
    invalid = sum(
        1 for v in price_data.values()
        if isinstance(v, str) and "Invalid Crumb" in v
    )
    return invalid / len(price_data) > threshold


class CrumbRejected(Exception):
    """Raised inside a fetch closure when Yahoo's response indicates IP blocking.

    :meth:`YahooOptionProvider._call` catches this, retries once with a fresh
    session, then trips the circuit breaker and returns the caller's
    fallback.
    """


def check_crumb(data: object) -> None:
    """Raise :class:`CrumbRejected` if ``data`` looks like a Yahoo IP block.

    Pass the raw dict returned by ``Ticker.<endpoint>``. Non-dicts are
    treated as non-rejections (Yahoo returns DataFrames for ``history``
    and string errors only for known-bad symbols).
    """
    if isinstance(data, dict) and crumb_rejected(data):
        raise CrumbRejected()
