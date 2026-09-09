"""The read side has to agree with the collector about what a contract is worth.

``enrich`` used to recompute the mark from bid and ask alone. Eurex publishes
no order book, so both are NULL for every European contract and the whole
snapshot came back with no marks at all, while the API response for the same
chain carried them from a different code path. Nothing read
``ChainSnapshot.mark``, which is what made it a trap rather than a bug.
"""

import os
from dataclasses import replace
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.domain.venue import Venue
from app.models import Group  # noqa: F401  (schema registration; see conftest)
from app.services.compute.chain import enrich
from tests.factories import make_chain

NOW = datetime(2026, 8, 21, 14, 35, tzinfo=timezone.utc)
SESSION = date(2026, 9, 8)
BERLIN = ZoneInfo("Europe/Berlin")


def _columns(chain, *, quoted: bool):
    """bid/ask arrays for a chain, all-NaN when the venue has no book."""
    n = len(chain.quotes)
    if not quoted:
        return np.full(n, np.nan), np.full(n, np.nan)
    return (
        np.array([q.bid for q in chain.quotes], dtype=float),
        np.array([q.ask for q in chain.quotes], dtype=float),
    )


def test_a_stored_mark_survives_a_chain_with_no_order_book():
    chain = make_chain()
    bid, ask = _columns(chain, quoted=False)
    stored = np.arange(1.0, len(chain.quotes) + 1.0)

    snap = enrich(
        refs=[q.ref for q in chain.quotes], now=NOW, spot=chain.spot,
        bid=bid, ask=ask, stored_mark=stored,
    )

    assert np.array_equal(snap.mark, stored)


def test_without_a_stored_column_the_mark_is_still_the_mid():
    """The un-persisted fetch path, which has no stored column to read."""
    chain = make_chain()
    bid, ask = _columns(chain, quoted=True)

    snap = enrich(refs=[q.ref for q in chain.quotes], now=NOW, spot=chain.spot,
                  bid=bid, ask=ask)

    assert np.allclose(snap.mark, (bid + ask) / 2)


def test_a_missing_stored_mark_falls_back_to_the_mid():
    """Mixed rows, so the fallback is per contract and not per chain."""
    chain = make_chain()
    bid, ask = _columns(chain, quoted=True)
    stored = np.full(len(chain.quotes), np.nan)
    stored[0] = 42.0

    snap = enrich(refs=[q.ref for q in chain.quotes], now=NOW, spot=chain.spot,
                  bid=bid, ask=ask, stored_mark=stored)

    assert snap.mark[0] == 42.0
    assert np.allclose(snap.mark[1:], ((bid + ask) / 2)[1:])


needs_postgres = pytest.mark.skipif(
    "postgresql" not in os.getenv("SKEWER_TEST_DATABASE_URL", ""),
    reason="Persisting a chain needs PostgreSQL (ON CONFLICT); set SKEWER_TEST_DATABASE_URL",
)


@needs_postgres
async def test_a_settled_european_chain_reads_back_with_marks(db):
    """The regression itself, end to end: collector writes, read path reads."""
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services.chain_service import load_snapshot_for
    from app.services.chain_sync import persist_chain

    underlying = await UnderlyingRepository(db).create("BMW.DE", "BMW", venue=Venue.EUREX)

    # A Eurex chain: no book at all, a settlement price on every contract.
    chain = make_chain(today=SESSION)
    chain = replace(
        chain, venue=Venue.EUREX, session_date=SESSION,
        quotes=[
            replace(q, bid=None, ask=None, last=None, settlement=(q.bid + q.ask) / 2)
            for q in chain.quotes
        ],
    )

    close = datetime.combine(SESSION, time(17, 30), tzinfo=BERLIN)
    result = await persist_chain(
        db, underlying.id, chain, full=True, now=close, settled=True
    )
    assert result.persisted == len(chain.quotes)

    snap, _, _, _ = await load_snapshot_for(db, underlying)

    assert np.isfinite(snap.mark).sum() == len(chain.quotes)
