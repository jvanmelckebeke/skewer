"""The weekend catalogue refresh registers contracts and stores no prices.

The job's docstring used to claim ``full=False`` achieved this on its own,
because no cadence bucket's slot aligns outside market hours. It does not:
``should_persist`` is a pure function of tenor and slot index, so the 12:30 UTC
run wrote a full frozen pre-market cross-section every day of the week.
"""

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.domain.venue import Venue
from app.models import Group
from tests.factories import make_chain

# A Saturday, well outside any session.
OFF_SESSION = datetime(2026, 9, 5, 12, 30, tzinfo=timezone.utc)

needs_postgres = pytest.mark.skipif(
    "postgresql" not in os.getenv("SKEWER_TEST_DATABASE_URL", ""),
    reason="Upserting a chain needs PostgreSQL (ON CONFLICT); set SKEWER_TEST_DATABASE_URL",
)


@needs_postgres
async def test_catalog_only_registers_contracts_but_stores_no_quotes(db):
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services.chain_sync import persist_chain

    underlying = await UnderlyingRepository(db).create("NVDA", "NVIDIA", venue=Venue.OCC)
    chain = make_chain()

    result = await persist_chain(
        db, underlying.id, chain, now=OFF_SESSION, catalog_only=True
    )

    assert result.new_contracts == len(chain.quotes)
    assert result.persisted == 0
    # Nothing was observed, so there is no instant to claim one was.
    assert result.slot is None

    for table, column in (("option_quotes", "contract_id"), ("option_daily_stats", "contract_id")):
        stored = await db.execute(
            text(
                f"select count(*) from {table} t"
                f" join option_contracts c on c.id = t.{column}"
                " where c.underlying_id = :uid"
            ),
            {"uid": underlying.id},
        )
        assert stored.scalar_one() == 0, f"{table} should be empty after a catalogue run"

    spot_rows = await db.execute(
        text("select count(*) from underlying_quotes where underlying_id = :uid"),
        {"uid": underlying.id},
    )
    assert spot_rows.scalar_one() == 0


@needs_postgres
async def test_a_normal_run_on_the_same_chain_does_store_quotes(db):
    """The control. Without it the test above passes on a broken fetch."""
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services.chain_sync import persist_chain

    underlying = await UnderlyingRepository(db).create("NVDA", "NVIDIA", venue=Venue.OCC)

    result = await persist_chain(db, underlying.id, make_chain(), full=True, now=OFF_SESSION)

    assert result.persisted > 0
    assert result.slot is not None


async def test_a_catalogue_run_does_not_count_as_a_poll(db, monkeypatch):
    """``last_polled_at`` answers "is the collector alive", not "has anything
    been registered". A catalogue run touches no feed the reader cares about,
    and marking it would hide a poll that has been failing all weekend."""
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services import chain_sync

    repo = UnderlyingRepository(db)
    underlying = await repo.create("NVDA", "NVIDIA", venue=Venue.OCC)
    group = Group(name="Watchlist", is_default=True, position=0)
    group.underlyings.append(underlying)
    db.add(group)
    await db.commit()

    async def fake_sync(db_, uid, symbol, div=0.0, **kwargs):
        assert kwargs["catalog_only"] is True
        return chain_sync.SyncResult(symbol, 40, 0, 40, 0, 0, None)

    monkeypatch.setattr(chain_sync, "sync_underlying", fake_sync)

    await chain_sync.sync_all(db, catalog_only=True, now=OFF_SESSION)

    refreshed = await repo.get_by_symbol("NVDA")
    assert refreshed.last_polled_at is None


async def test_the_catalogue_job_asks_for_a_catalogue_run(monkeypatch):
    """Guards the wiring, which is where the original bug lived. The pipeline
    was always capable of not persisting, the job just never asked."""
    import contextlib

    from app.background_tasks import jobs

    seen = {}

    @contextlib.asynccontextmanager
    async def fake_session():
        yield None

    async def fake_sync_all(db, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(jobs, "async_session", fake_session)
    monkeypatch.setattr(jobs, "sync_all", fake_sync_all)

    await jobs.refresh_contract_catalog()

    assert seen.get("catalog_only") is True
    assert not seen.get("full")
