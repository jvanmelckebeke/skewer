"""Tests for the Yahoo Finance rate limiter and circuit breaker."""

import threading
import time as _time

import pytest

from app.services.option_providers.yahoo_rate_limit import (
    CrumbRejected,
    YahooThrottle,
    check_crumb,
    crumb_rejected,
)


class TestCrumbRejected:
    def test_empty_dict_is_false(self):
        assert crumb_rejected({}) is False

    def test_none_is_false(self):
        assert crumb_rejected(None) is False

    def test_all_invalid_is_true(self):
        assert crumb_rejected({"A": "Invalid Crumb", "B": "Invalid Crumb"}) is True

    def test_majority_invalid_is_true(self):
        # 2/3 = 66% > 50% threshold.
        assert crumb_rejected({"A": "Invalid Crumb", "B": "Invalid Crumb", "C": {}}) is True

    def test_exactly_half_invalid_is_false(self):
        # 50% is not strictly more than threshold (0.5).
        assert crumb_rejected({"A": "Invalid Crumb", "B": {}}) is False

    def test_minority_invalid_is_false(self):
        # 1/3 = 33% — likely a missing/bad symbol, not IP block.
        assert crumb_rejected({"A": "Invalid Crumb", "B": {}, "C": {}}) is False

    def test_threshold_param(self):
        # 1/2 = 50% counts when threshold drops to 0.4.
        assert crumb_rejected({"A": "Invalid Crumb", "B": {}}, threshold=0.4) is True


class TestCheckCrumb:
    def test_raises_on_majority_invalid(self):
        with pytest.raises(CrumbRejected):
            check_crumb({"A": "Invalid Crumb", "B": "Invalid Crumb"})

    def test_no_raise_on_clean_dict(self):
        check_crumb({"A": {"price": 1}})

    def test_no_raise_on_non_dict(self):
        # History returns a DataFrame, not a dict — must not falsely trigger.
        check_crumb([1, 2, 3])
        check_crumb("error string")
        check_crumb(None)


class TestYahooThrottleSpacing:
    def test_first_call_returns_immediately(self):
        t = YahooThrottle(min_interval=0.1)
        start = _time.monotonic()
        t.wait()
        assert _time.monotonic() - start < 0.05

    def test_second_call_waits_min_interval(self):
        t = YahooThrottle(min_interval=0.1)
        t.wait()
        start = _time.monotonic()
        t.wait()
        elapsed = _time.monotonic() - start
        assert 0.08 <= elapsed <= 0.2

    def test_wait_unblocks_after_min_interval_already_elapsed(self):
        t = YahooThrottle(min_interval=0.05)
        t.wait()
        _time.sleep(0.06)
        start = _time.monotonic()
        t.wait()
        # Already past the interval, no extra wait.
        assert _time.monotonic() - start < 0.02


class TestYahooThrottleBreaker:
    def test_initial_not_blocked(self):
        t = YahooThrottle(min_interval=0.0)
        assert t.is_blocked() is False
        assert t.time_until_unblocked() == 0.0

    def test_record_invalid_crumb_trips_breaker(self):
        t = YahooThrottle(min_interval=0.0, backoff_schedule=(0.1, 0.2, 0.3))
        t.record_invalid_crumb()
        assert t.is_blocked() is True
        assert 0.05 < t.time_until_unblocked() <= 0.1

    def test_breaker_clears_after_window(self):
        t = YahooThrottle(min_interval=0.0, backoff_schedule=(0.05, 0.1))
        t.record_invalid_crumb()
        assert t.is_blocked() is True
        _time.sleep(0.07)
        assert t.is_blocked() is False

    def test_record_success_resets_failure_counter(self):
        t = YahooThrottle(min_interval=0.0, backoff_schedule=(0.05, 0.5))
        t.record_invalid_crumb()
        assert t._consecutive_failures == 1
        _time.sleep(0.06)
        t.record_success()
        assert t._consecutive_failures == 0
        assert t.is_blocked() is False

    def test_escalating_backoff(self):
        """Each consecutive failure picks the next entry in the schedule."""
        t = YahooThrottle(min_interval=0.0, backoff_schedule=(0.05, 0.1, 0.2))
        t.record_invalid_crumb()
        first = t.time_until_unblocked()
        _time.sleep(0.06)
        t.record_invalid_crumb()
        second = t.time_until_unblocked()
        _time.sleep(0.11)
        t.record_invalid_crumb()
        third = t.time_until_unblocked()

        assert first <= 0.05
        assert 0.05 < second <= 0.1
        assert 0.1 < third <= 0.2

    def test_backoff_holds_at_max(self):
        """Failures past the schedule length stay at the longest backoff."""
        t = YahooThrottle(min_interval=0.0, backoff_schedule=(0.05, 0.1))
        t.record_invalid_crumb()  # 0.05
        _time.sleep(0.06)
        t.record_invalid_crumb()  # 0.1
        _time.sleep(0.11)
        t.record_invalid_crumb()  # held at 0.1
        held = t.time_until_unblocked()
        assert 0.05 < held <= 0.1


class TestYahooThrottleConcurrent:
    def test_concurrent_waiters_respect_spacing(self):
        """Two threads calling wait() at the same time return spaced ≥ min_interval apart."""
        t = YahooThrottle(min_interval=0.1)
        return_times: list[float] = []
        lock = threading.Lock()

        def worker():
            t.wait()
            with lock:
                return_times.append(_time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        return_times.sort()
        # 3 calls, 2 gaps of >= ~0.1s each.
        assert return_times[1] - return_times[0] >= 0.08
        assert return_times[2] - return_times[1] >= 0.08

    def test_breaker_blocks_subsequent_threads(self):
        """When the breaker is open, threads still calling wait() see a long sleep."""
        t = YahooThrottle(min_interval=0.0, backoff_schedule=(0.1,))
        t.record_invalid_crumb()
        assert t.is_blocked() is True

        start = _time.monotonic()
        t.wait()
        elapsed = _time.monotonic() - start
        # wait() must have slept until the breaker reopened.
        assert 0.05 <= elapsed <= 0.2


class TestYahooThrottleReset:
    def test_reset_clears_all_state(self):
        t = YahooThrottle(min_interval=0.5)
        t.wait()
        t.record_invalid_crumb()
        assert t._consecutive_failures > 0
        assert t._blocked_until > 0
        t.reset()
        assert t._consecutive_failures == 0
        assert t._blocked_until == 0.0
        assert t._last_call_at == 0.0
