"""The session-profile read path.

Requires PostgreSQL: the query uses ordered-set aggregates
(``percentile_cont ... WITHIN GROUP``) and ``timezone()``, neither of which
SQLite has. Set SKEWER_TEST_DATABASE_URL to run.
"""

import os
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.chain_service import session_profile
from app.services.chain_sync import persist_chain
from tests.factories import make_chain

pytestmark = pytest.mark.skipif(
    "postgresql" not in os.getenv("SKEWER_TEST_DATABASE_URL", ""),
    reason="Session-profile tests need PostgreSQL (ordered-set aggregates); set SKEWER_TEST_DATABASE_URL",
)

TODAY = date(2026, 8, 21)
# 14:35 UTC is 10:35 in New York, so the exchange-local session date matches
# the UTC one — a slot near midnight UTC would not, which is the whole reason
# the query converts before truncating.
FIRST_SLOT = datetime(2026, 8, 21, 14, 35, tzinfo=timezone.utc)


async def _underlying(db, symbol="TEST"):
    from app.repositories.underlying_repo import UnderlyingRepository

    return await UnderlyingRepository(db).create(symbol, f"{symbol} Inc")


async def _persist_slots(db, underlying_id, count=4, spots=None):
    """Write `count` consecutive 5-minute slots, optionally moving spot."""
    for i in range(count):
        spot = spots[i] if spots else 100.0
        chain = make_chain(symbol="TEST", today=TODAY, spot=spot)
        await persist_chain(
            db, underlying_id, chain, full=True, now=FIRST_SLOT + timedelta(minutes=5 * i)
        )


async def test_profile_reports_one_row_per_contract_with_quantiles(db):
    u = await _underlying(db)
    await _persist_slots(db, u.id, count=4)

    result = await session_profile(db, "TEST", expiration=TODAY + timedelta(days=3), session=TODAY)

    assert result["session_date"] == TODAY
    assert result["slots"] == 4
    assert result["points"], "expected one point per contract in the expiration"
    # The factory builds both rights across five strikes.
    assert len({p["strike"] for p in result["points"]}) == 5
    assert {p["right"] for p in result["points"]} == {"call", "put"}

    for point in result["points"]:
        assert point["samples"] == 4
        if point["iv_p50"] is not None:
            # Quantiles are ordered by construction; a violation means the
            # WITHIN GROUP ordering was lost.
            assert point["iv_min"] <= point["iv_p25"] <= point["iv_p50"] <= point["iv_p75"]
            assert point["iv_p75"] <= point["iv_max"]


async def test_spot_range_widens_when_the_underlying_moves(db):
    u = await _underlying(db)
    await _persist_slots(db, u.id, count=3, spots=[100.0, 104.0, 102.0])

    result = await session_profile(db, "TEST", expiration=TODAY + timedelta(days=3), session=TODAY)

    assert result["spot_low"] == pytest.approx(100.0)
    assert result["spot_high"] == pytest.approx(104.0)


async def test_available_sessions_carry_slot_counts(db):
    u = await _underlying(db)
    await _persist_slots(db, u.id, count=2)

    result = await session_profile(db, "TEST", session=TODAY)

    assert result["available_sessions"] == [{"session_date": TODAY, "slots": 2}]


async def test_expiration_defaults_past_the_same_day_expiry(db):
    """The nearest expiry that is not expiring today, since a 0DTE contract's
    implied volatility is not a representative landing view."""
    u = await _underlying(db)
    await _persist_slots(db, u.id, count=2)

    result = await session_profile(db, "TEST", session=TODAY)

    assert result["expiration"] > TODAY
    assert TODAY + timedelta(days=3) == result["expiration"]
    assert result["available_expirations"][0] == TODAY + timedelta(days=3)


async def test_unknown_session_is_a_404_not_an_empty_profile(db):
    from fastapi import HTTPException

    u = await _underlying(db)
    await _persist_slots(db, u.id, count=2)

    with pytest.raises(HTTPException) as excinfo:
        await session_profile(db, "TEST", session=date(2020, 1, 2))
    assert excinfo.value.status_code == 404
