"""Adaptive cadence — the capture policy."""

from datetime import datetime, timedelta, timezone

from app.domain.cadence import (
    bucket_for,
    expected_rows_per_session,
    floor_to_slot,
    should_persist,
    slot_index,
)

BASE = 5
T0 = datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc)


def test_buckets_by_tenor():
    assert bucket_for(0).name == "gamma"
    assert bucket_for(7).name == "gamma"
    assert bucket_for(8).name == "front"
    assert bucket_for(30).name == "front"
    assert bucket_for(31).name == "mid"
    assert bucket_for(90).name == "mid"
    assert bucket_for(91).name == "leaps"
    assert bucket_for(900).name == "leaps"


def test_expired_contracts_are_never_persisted():
    assert bucket_for(-1).name == "expired"
    assert not should_persist(-1, T0, BASE)


def test_leaps_are_not_persisted_intraday():
    """They get exactly one row per session from the EOD job instead."""
    assert all(not should_persist(400, T0 + timedelta(minutes=5 * i), BASE) for i in range(24))


def test_cadence_spacing_matches_the_bucket():
    def hits(dte, n=36):
        return [i for i in range(n) if should_persist(dte, T0 + timedelta(minutes=5 * i), BASE)]

    assert hits(3) == list(range(36))                    # every 5 min
    assert hits(20) == [i for i in range(36) if i % 3 == 0]   # every 15 min
    assert hits(60) == [i for i in range(36) if i % 6 == 0]   # every 30 min


def test_slots_are_continuous_across_midnight():
    """Counting from a fixed epoch, not from local midnight — otherwise a
    30-minute bucket can emit two samples 5 minutes apart at 00:00."""
    before = datetime(2026, 8, 21, 23, 55, tzinfo=timezone.utc)
    after = datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)
    assert slot_index(after, BASE) - slot_index(before, BASE) == 1


def test_floor_to_slot_snaps_down():
    ts = datetime(2026, 8, 21, 14, 37, 42, tzinfo=timezone.utc)
    assert floor_to_slot(ts, 5) == datetime(2026, 8, 21, 14, 35, tzinfo=timezone.utc)


def test_all_contracts_in_one_poll_share_a_timestamp():
    """What makes a stored snapshot a genuine simultaneous cross-section."""
    a = datetime(2026, 8, 21, 14, 35, 2, tzinfo=timezone.utc)
    b = datetime(2026, 8, 21, 14, 39, 59, tzinfo=timezone.utc)
    assert floor_to_slot(a, 5) == floor_to_slot(b, 5)


def test_adaptive_cadence_cuts_volume_substantially():
    """Measured against the real NVDA tenor distribution."""
    dte_counts = {0: 193, 3: 98, 7: 133, 14: 102, 21: 97, 28: 148,
                  35: 86, 42: 62, 56: 146, 91: 127, 119: 776, 500: 1318}
    total = sum(dte_counts.values())
    naive = total * (390 / BASE)
    adaptive = expected_rows_per_session(dte_counts, BASE)
    assert adaptive < naive * 0.35
