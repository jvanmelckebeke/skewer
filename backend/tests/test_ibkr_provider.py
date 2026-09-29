"""Eurex intraday through IBKR: stagger, quote rules, and slot stamping."""

import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.domain.cadence import should_persist
from app.domain.contracts import ContractRef, Right
from app.domain.venue import Venue
from app.services.ibkr.base import Quote, SnapshotJob
from app.services.option_providers import ibkr as ibkr_mod
from app.services.option_providers.base import ProviderUnavailable
from app.services.option_providers.ibkr import IbkrOptionProvider, Quotable

BERLIN = ZoneInfo("Europe/Berlin")
BASE = 5
# Slot 09:50 Berlin; with the 15-minute delay its window is 10:05-10:10 local.
SLOT = datetime(2026, 9, 30, 9, 50, tzinfo=BERLIN).astimezone(timezone.utc)
IN_WINDOW = SLOT + timedelta(minutes=16)
LATE = SLOT + timedelta(minutes=21)


# ------------------------------------------------------------------ stagger


def test_staggering_spreads_a_bucket_without_changing_its_interval():
    """Each mid-bucket expiry is taken exactly once per 30 minutes, on its own slot."""
    start = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
    slots = [start + timedelta(minutes=BASE * i) for i in range(6)]
    expiries = [date(2026, 11, 20) + timedelta(days=7 * k) for k in range(6)]
    on = start.date()

    taken = {e: [s for s in slots if should_persist((e - on).days, s, BASE, stagger=e)] for e in expiries}

    assert all(len(hits) == 1 for hits in taken.values())
    assert len({hits[0] for hits in taken.values()}) > 1


def test_without_a_stagger_the_bucket_lands_on_one_slot():
    start = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
    slots = [start + timedelta(minutes=BASE * i) for i in range(6)]

    hits = [s for s in slots if should_persist(60, s, BASE)]

    assert len(hits) == 1


def test_the_gamma_bucket_is_every_slot_either_way():
    slot = datetime(2026, 9, 30, 8, 5, tzinfo=timezone.utc)
    assert should_persist(3, slot, BASE, stagger=date(2026, 10, 2))


# ------------------------------------------------------------- the provider


def _ref(symbol, strike, right=Right.CALL, cid=1):
    return ContractRef(
        symbol=symbol, venue=Venue.EUREX, root="BMW", expiration=date(2026, 10, 16),
        right=right, strike=strike, currency="EUR", id=cid,
    )


def _q(con_id, **kw):
    kw.setdefault("market_data_type", "delayed")
    kw.setdefault("receive_time", IN_WINDOW)
    return Quote(conId=con_id, **kw)


class FakeClient:
    def __init__(self, job: SnapshotJob, running_polls: int = 0):
        self.job = job
        self.running_polls = running_polls
        self.submitted = None

    async def submit_snapshots(self, con_ids, underlying, priority="bulk"):
        self.submitted = (con_ids, underlying, priority)
        return SnapshotJob(id="j1", status="running") if self.running_polls else self.job

    async def snapshot_job(self, job_id):
        self.running_polls -= 1
        return self.job if self.running_polls <= 0 else SnapshotJob(id="j1", status="running")


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(ibkr_mod, "POLL_INTERVAL_SECONDS", 0.0)


CONTRACTS = [
    Quotable(_ref("EUX:A", 56.0, cid=11), 101),
    Quotable(_ref("EUX:B", 57.0, cid=12), 102),
    Quotable(_ref("EUX:C", 58.0, cid=13), 103),
    Quotable(_ref("EUX:D", 59.0, cid=14), 104),
    Quotable(_ref("EUX:E", 60.0, cid=15), 105),
]


async def test_only_usable_quotes_are_kept():
    job = SnapshotJob(id="j1", status="done", quotes=[
        _q(101, bid=1.0, ask=1.2),
        _q(102, bid=None, ask=0.05),  # far OTM, one-sided: kept, bid stays null
        _q(103, error="354: not subscribed"),
        _q(104, bid=1.0, ask=1.2, market_data_type="delayed_frozen"),
        _q(105, bid=1.0, ask=1.2, receive_time=LATE),
    ], underlying=[
        _q(14171, bid=53.5, ask=53.7),
        _q(14171, bid=53.7, ask=53.9),
    ])
    provider = IbkrOptionProvider(gateway=FakeClient(job, running_polls=2))

    chain = await provider.fetch_subset("BMW.DE", 14171, CONTRACTS, SLOT, BASE)

    assert [q.ref.symbol for q in chain.quotes] == ["EUX:A", "EUX:B"]
    assert chain.quotes[1].bid is None
    assert chain.spot == pytest.approx(53.7)  # mean of the two bracketing mids
    assert chain.session_date == date(2026, 9, 30)
    assert chain.spot_currency is None


async def test_a_live_quote_on_a_delayed_venue_is_dropped():
    job = SnapshotJob(id="j1", status="done", quotes=[_q(101, bid=1.0, ask=1.2, market_data_type="live")])

    chain = await IbkrOptionProvider(gateway=FakeClient(job)).fetch_subset(
        "BMW.DE", 14171, CONTRACTS[:1], SLOT, BASE
    )

    assert chain.quotes == []


async def test_a_failed_job_is_provider_unavailable():
    job = SnapshotJob(id="j1", status="failed", error="gateway disconnected")

    with pytest.raises(ProviderUnavailable, match="gateway disconnected"):
        await IbkrOptionProvider(gateway=FakeClient(job)).fetch_subset(
            "BMW.DE", 14171, CONTRACTS[:1], SLOT, BASE
        )


# ------------------------------------------------------ the sync path (PG)


needs_postgres = pytest.mark.skipif(
    "postgresql" not in os.getenv("SKEWER_TEST_DATABASE_URL", ""),
    reason="Persisting quotes needs PostgreSQL (ON CONFLICT); set SKEWER_TEST_DATABASE_URL",
)


@needs_postgres
async def test_an_intraday_poll_is_stamped_start_minus_delay(db, monkeypatch):
    from sqlalchemy import select

    from app.models import OptionContract, OptionQuote
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services import chain_sync

    underlying = await UnderlyingRepository(db).create("BMW.DE", "BMW", currency="EUR", venue=Venue.EUREX)
    seen = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)
    near = OptionContract(
        underlying_id=underlying.id, symbol="EUX:NEAR", venue=Venue.EUREX,
        expiration=date(2026, 10, 2), strike=54.0, right=Right.CALL, contract_size=100,
        currency="EUR", last_seen_at=seen, ibkr_con_id=101,
    )
    unmatched = OptionContract(
        underlying_id=underlying.id, symbol="EUX:NOCONID", venue=Venue.EUREX,
        expiration=date(2026, 10, 2), strike=55.0, right=Right.CALL, contract_size=100,
        currency="EUR", last_seen_at=seen,
    )
    db.add_all([near, unmatched])
    await db.commit()

    job = SnapshotJob(id="j1", status="done", quotes=[_q(101, bid=1.0, ask=1.2)],
                      underlying=[_q(14171, bid=53.5, ask=53.7)])
    client = FakeClient(job)
    monkeypatch.setattr(chain_sync, "intraday_for", lambda venue: IbkrOptionProvider(gateway=client))

    started = datetime.combine(date(2026, 9, 30), time(10, 7), tzinfo=BERLIN).astimezone(timezone.utc)
    result = await chain_sync.sync_underlying(
        db, underlying.id, "BMW.DE", venue=Venue.EUREX, ibkr_con_id=14171, poll_started=started,
    )

    assert client.submitted == ([101], 14171, "bulk")
    assert result.slot == SLOT
    stored = (await db.execute(select(OptionQuote.ts, OptionQuote.mark))).all()
    assert stored == [(SLOT, pytest.approx(1.1))]
    await db.refresh(near)
    assert near.last_seen_at == seen  # a quote feed is not the catalog
