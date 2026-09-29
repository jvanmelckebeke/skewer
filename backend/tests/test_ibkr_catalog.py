"""The daily IBKR catalog pass, end to end against a fake ibkr-svc."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest_asyncio

from app.domain.contracts import Right
from app.domain.venue import Venue
from app.services.ibkr.base import ContractInfo
from app.services.ibkr.catalog import link_contracts
from app.services.ibkr.venues import IBKR_VENUES

TODAY = date(2026, 9, 29)
OCT = date(2026, 10, 16)
SEEN = datetime(2026, 9, 28, 19, 59, tzinfo=timezone.utc)
BMW_STOCK = 14171


def _ib(con_id, cls, strike, right, expiry=OCT):
    return ContractInfo(
        conId=con_id, tradingClass=cls, lastTradeDateOrContractMonth=expiry.strftime("%Y%m%d"),
        strike=strike, right=right, multiplier="100", underConId=BMW_STOCK,
    )


class FakeIbkr:
    def __init__(self):
        self.resolved: list[str] = []
        self.chains_fetched: list[int] = []
        self.chain = [
            _ib(101, "BMW", 56.0, "C"),
            _ib(102, "BMW", 56.0, "P"),
            _ib(201, "BMWE", 56.0, "C"),  # the European twin, same terms
        ]

    async def resolve_isin(self, isin):
        self.resolved.append(isin)
        # Two listings, as for a dual-listed share; only the EUR one is BMW's.
        return [
            ContractInfo(conId=999, secType="STK", symbol="BMWYY", currency="USD"),
            ContractInfo(conId=BMW_STOCK, secType="STK", symbol="BMW", currency="EUR"),
            ContractInfo(conId=BMW_STOCK, secType="STK", symbol="BMW", currency="EUR"),
        ]

    async def option_contracts(self, under_con_id, exchange="EUREX", max_dte=None):
        self.chains_fetched.append(under_con_id)
        return self.chain if under_con_id == BMW_STOCK else []


async def _isin(product):
    return ("DE0005190003", "EUR") if product == "BMW" else None


# The real Eurex entry, with Deutsche Boerse's ISIN lookup swapped for a fake.
EUREX = replace(IBKR_VENUES[Venue.EUREX], underlying_isin=_isin)


@pytest_asyncio.fixture
async def bmw(db):
    from app.models import Group, OptionContract
    from app.models.group import group_underlyings
    from app.repositories.underlying_repo import UnderlyingRepository

    group = Group(name="Watchlist", is_default=True, position=0)
    db.add(group)
    await db.commit()
    row = await UnderlyingRepository(db).create("BMW.DE", "BMW", currency="EUR", venue=Venue.EUREX)
    await db.execute(group_underlyings.insert().values(group_id=group.id, underlying_id=row.id))
    for symbol, strike, right, seen in (
        ("EUX:DE000C1", 56.0, Right.CALL, SEEN),
        ("EUX:DE000P1", 56.0, Right.PUT, SEEN),
        # No longer in the Eurex catalog: last seen days before the newest row.
        ("EUX:DE000OLD", 56.0, Right.PUT, SEEN - timedelta(days=5)),
    ):
        db.add(OptionContract(
            underlying_id=row.id, symbol=symbol, venue=Venue.EUREX, expiration=OCT,
            strike=strike, right=right, contract_size=100, currency="EUR", last_seen_at=seen,
        ))
    await db.commit()
    return row


async def _con_ids(db):
    from sqlalchemy import select

    from app.models import OptionContract

    rows = await db.execute(select(OptionContract.symbol, OptionContract.ibkr_con_id))
    return dict(rows.all())


async def test_resolves_the_underlying_then_links_its_contracts(db, bmw):
    client = FakeIbkr()

    [report] = await link_contracts(db, client, TODAY, venues=[EUREX])

    assert client.resolved == ["DE0005190003"]
    # The listing in the product's currency is verified first, and it is enough.
    assert client.chains_fetched[0] == BMW_STOCK
    await db.refresh(bmw)
    assert bmw.ibkr_con_id == BMW_STOCK
    assert await _con_ids(db) == {"EUX:DE000C1": 101, "EUX:DE000P1": 102, "EUX:DE000OLD": None}
    assert (report.linked, report.written, report.ambiguous) == (2, 2, 0)


async def test_a_second_pass_changes_nothing_and_skips_resolution(db, bmw):
    client = FakeIbkr()
    await link_contracts(db, client, TODAY, venues=[EUREX])
    client.resolved.clear()

    [report] = await link_contracts(db, client, TODAY, venues=[EUREX])

    assert client.resolved == []
    assert (report.linked, report.written) == (2, 0)


async def test_an_underlying_whose_catalog_lacks_the_product_stays_unresolved(db, bmw):
    client = FakeIbkr()
    client.chain = [_ib(301, "SAP", 56.0, "C")]

    [report] = await link_contracts(db, client, TODAY, venues=[EUREX])

    await db.refresh(bmw)
    assert bmw.ibkr_con_id is None
    assert report.error == "underlying not resolved"


# ------------------------------------------------------------ venue data


def test_only_eurex_is_enabled_by_default():
    from app.services.ibkr.venues import enabled_venues

    assert [v.venue for v in enabled_venues()] == [Venue.EUREX]


def test_euronext_maps_its_market_to_ibkrs_exchange():
    euronext = IBKR_VENUES[Venue.EURONEXT]

    assert euronext.product_for("PHIA.AS", None) == ("FTA", "PHIA")
    assert euronext.product_for("ABI.BR", "DBRU:ABI") == ("BELFOX", "ABI")
    assert euronext.product_for("EDP.LS", "DLIS:EDP") is None  # Lisbon: unverified


async def test_a_venue_without_an_isin_source_needs_a_hand_set_con_id(db, bmw):
    from app.models.group import group_underlyings
    from app.repositories.underlying_repo import UnderlyingRepository

    row = await UnderlyingRepository(db).create("PHIA.AS", "Philips", currency="EUR", venue=Venue.EURONEXT)
    group_id = (await db.execute(group_underlyings.select())).first().group_id
    await db.execute(group_underlyings.insert().values(group_id=group_id, underlying_id=row.id))
    await db.commit()

    reports = await link_contracts(db, FakeIbkr(), TODAY, venues=[EUREX, IBKR_VENUES[Venue.EURONEXT]])

    by_symbol = {r.symbol: r for r in reports}
    assert by_symbol["PHIA.AS"].error == "no underlying ISIN"
    assert by_symbol["BMW.DE"].linked == 2
