"""The batch summary read path behind the overview.

Two halves. The shape of a row (every registered metric present, nulls where
the chain cannot answer) is pure enough to test on SQLite. Anything that needs
a *stored* chain needs PostgreSQL, because ``persist_chain`` upserts with
``ON CONFLICT``.
"""

import os
from datetime import date, datetime, timezone

import pytest

from app.services.chain_service import chain_summary
from app.services.compute.analytics import METRIC_REGISTRY

needs_postgres = pytest.mark.skipif(
    "postgresql" not in os.getenv("SKEWER_TEST_DATABASE_URL", ""),
    reason="Persisting a chain needs PostgreSQL (ON CONFLICT); set SKEWER_TEST_DATABASE_URL",
)

TODAY = date(2026, 8, 21)
SLOT = datetime(2026, 8, 21, 14, 35, tzinfo=timezone.utc)


async def _underlying(db, symbol="TEST"):
    from app.repositories.underlying_repo import UnderlyingRepository

    return await UnderlyingRepository(db).create(symbol, f"{symbol} Inc")


async def test_an_unpolled_underlying_is_a_row_of_nulls(db):
    """A ticker added a minute ago must appear, empty rather than absent."""
    await _underlying(db)

    rows = await chain_summary(db)

    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "TEST"
    assert row["ts"] is None
    assert row["spot"] is None
    # Never polled reads as None rather than as a very large number of minutes.
    assert row["minutes_since_poll"] is None
    assert isinstance(row["market_open"], bool)
    # Every metric key is present so the client can render fixed columns.
    assert set(row["metrics"]) == set(METRIC_REGISTRY)
    assert all(value is None for value in row["metrics"].values())


async def test_summary_is_reachable_ahead_of_the_symbol_route(client, db):
    """``/summary`` must not be captured by ``/{symbol}``."""
    await _underlying(db)

    response = await client.get("/api/chains/summary")

    assert response.status_code == 200
    assert [row["symbol"] for row in response.json()] == ["TEST"]


async def test_group_id_scopes_to_that_group(db):
    from app.services import group_service

    kept = await _underlying(db, "KEPT")
    await _underlying(db, "OTHER")
    group = await group_service.create_group(db, "Basket", None, None)
    await group_service.add_underlyings(db, group.id, [kept.id])

    rows = await chain_summary(db, group_id=group.id)

    assert [row["symbol"] for row in rows] == ["KEPT"]


async def test_unknown_group_is_a_404(db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        await chain_summary(db, group_id=9999)

    assert raised.value.status_code == 404


@needs_postgres
async def test_a_stored_chain_fills_in_spot_and_metrics(db):
    from app.services.chain_sync import persist_chain
    from tests.factories import make_chain

    underlying = await _underlying(db)
    await persist_chain(
        db, underlying.id, make_chain(symbol="TEST", today=TODAY, spot=100.0),
        full=True, now=SLOT,
    )

    rows = await chain_summary(db)

    row = rows[0]
    assert row["ts"] == SLOT
    assert row["spot"] == pytest.approx(100.0)
    # atm_iv is the one metric the factory's chain is built to be able to
    # answer; the rest are allowed to be null on a five-strike chain.
    assert row["metrics"]["atm_iv"] is not None
