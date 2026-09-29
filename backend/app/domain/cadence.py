"""Adaptive capture cadence.

Every poll fetches the **whole** chain — that is not a choice, it is what the
provider offers: Yahoo's options endpoint returns all expirations in a single
request (``getAllData=true``), so fetching one expiration costs the same as
fetching twenty. The adaptive part is therefore about *what we persist*, not
what we request. One network round-trip per underlying per base tick; a
per-contract decision about whether this tick becomes a stored row.

Why cadence rather than a strike band: a near-dated option's value moves on a
minute scale, while a 2028 LEAPS barely re-quotes within a day. Sampling both
at 5 minutes stores the same LEAPS number ~78 times a day. Sampling by tenor
keeps full strike coverage — every strike is still observed, so the IV smile
and skew are never truncated — while cutting stored rows by roughly two
thirds. Measured on the real NVDA chain (3,286 contracts, 20 expirations),
this is the difference between ~256k and ~85k rows per day for one underlying.

**Slot alignment.** A contract's bucket does not track "when did I last store
this" — that would need per-contract state and would drift. Instead each poll
is floored onto a wall-clock grid of ``base_poll_minutes``, giving a slot
index, and a bucket with interval ``N x base`` persists only on slots where
``slot % N == 0``. This is stateless, deterministic, and produces evenly
spaced series that line up across contracts — so a "snapshot" of the chain at
14:35 is a real simultaneous cross-section rather than a ragged smear, which
is what makes an IV surface meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True, slots=True)
class CadenceBucket:
    """One tenor band and how often contracts in it are stored."""

    name: str
    max_dte: int | None  # inclusive upper bound; None = open-ended
    interval_minutes: int

    def covers(self, dte: int) -> bool:
        return self.max_dte is None or dte <= self.max_dte


# Ordered by tenor; the first bucket whose bound covers the DTE wins.
#
# The boundaries follow how options actually behave rather than round numbers:
# under a week gamma dominates and prices move tick-to-tick; inside a month is
# the front-month bulk where most volume and the tradeable skew live; out to a
# quarter still re-prices meaningfully within a session; beyond that (LEAPS)
# a daily close is genuinely all the information there is.
CADENCE_BUCKETS: tuple[CadenceBucket, ...] = (
    CadenceBucket("gamma", max_dte=7, interval_minutes=5),
    CadenceBucket("front", max_dte=30, interval_minutes=15),
    CadenceBucket("mid", max_dte=90, interval_minutes=30),
    CadenceBucket("leaps", max_dte=None, interval_minutes=0),  # 0 = EOD snapshot only
)

# An expired contract is not in any bucket; it is dropped from polling entirely.
EXPIRED = CadenceBucket("expired", max_dte=None, interval_minutes=0)


def bucket_for(dte: int) -> CadenceBucket:
    """The cadence bucket a contract with ``dte`` days to expiry belongs to."""
    if dte < 0:
        return EXPIRED
    for bucket in CADENCE_BUCKETS:
        if bucket.covers(dte):
            return bucket
    return CADENCE_BUCKETS[-1]


def slot_index(ts: datetime, base_minutes: int) -> int:
    """The wall-clock slot ``ts`` falls in, counted from the UTC epoch day.

    Counting from a fixed epoch rather than from midnight keeps slots
    continuous across day boundaries, so a bucket's spacing never jumps at
    00:00 (which would otherwise put two 30-minute samples 5 minutes apart).
    """
    epoch_minutes = int(ts.timestamp()) // 60
    return epoch_minutes // base_minutes


def floor_to_slot(ts: datetime, base_minutes: int) -> datetime:
    """Snap ``ts`` down onto the poll grid.

    Every contract stored in one poll shares this timestamp, which is what
    makes a stored cross-section simultaneous — see the module docstring.
    """
    epoch = int(ts.timestamp())
    step = base_minutes * 60
    return datetime.fromtimestamp((epoch // step) * step, tz=ts.tzinfo)


def should_persist(
    dte: int, ts: datetime, base_minutes: int, stagger: date | None = None
) -> bool:
    """Whether a contract with ``dte`` days to expiry is stored on this tick.

    ``dte`` below zero (expired) and the LEAPS bucket both return False: LEAPS
    are captured by the end-of-day snapshot job, which bypasses this check.

    ``stagger`` (the contract's expiration) spreads a coarse bucket across the
    slots it spans, one expiry at a time, instead of taking the whole bucket on
    one slot. Every contract keeps its interval and an expiry stays a
    simultaneous cross-section; what changes is that a whole-chain snapshot
    is complete only across ``interval`` minutes, not on one slot. Used where
    each contract is a separate request (IBKR), so a 30-minute slot does not
    carry six times the requests of the 5-minute slots around it.
    """
    bucket = bucket_for(dte)
    if bucket.interval_minutes <= 0:
        return False
    every = max(1, bucket.interval_minutes // base_minutes)
    phase = stagger.toordinal() % every if stagger is not None else 0
    return slot_index(ts, base_minutes) % every == phase


def expected_rows_per_session(
    dte_counts: dict[int, int], base_minutes: int, session_minutes: int = 390
) -> float:
    """Estimated stored rows per session for a chain's DTE distribution.

    Exposed so capacity can be *measured against the real chain* rather than
    guessed at — the retention and compression settings in the migration were
    sized with this. ``dte_counts`` maps days-to-expiry to contract count.
    """
    ticks = session_minutes / base_minutes
    total = 0.0
    for dte, count in dte_counts.items():
        bucket = bucket_for(dte)
        if bucket.interval_minutes <= 0:
            total += count  # one end-of-day snapshot row
        else:
            every = max(1, bucket.interval_minutes // base_minutes)
            total += count * (ticks / every)
    return total


def next_expirations(expirations: list[date], on: date, limit: int) -> list[date]:
    """The ``limit`` nearest non-expired expirations, ascending."""
    return sorted(e for e in expirations if (e - on).days >= 0)[:limit]


def session_slots(start: datetime, end: datetime, base_minutes: int) -> list[datetime]:
    """Every poll instant on the grid within ``[start, end)`` — used by tests
    and by capacity estimates, not by the scheduler itself."""
    out, cur = [], floor_to_slot(start, base_minutes)
    step = timedelta(minutes=base_minutes)
    while cur < end:
        if cur >= start:
            out.append(cur)
        cur += step
    return out
