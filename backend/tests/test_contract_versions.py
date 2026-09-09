"""Corporate actions on Eurex, where a contract's terms are not immutable.

The dimension table assumes strike, expiration and right never change. That is
true on OCC, where the terms are encoded in the symbol. On Eurex an adjustment
issues a new ISIN with a new strike and sometimes a new contract size, so the
position continues in a different row and the old one keeps its history.

Live shape, SAP on 2026-09-09: 1,284 contracts at version 0 / generation 8,
and 4 at version 1 / generation 7 carrying strike 91.49 against an original
strike of 92, on 101 shares rather than 100.
"""

import os
from datetime import date, datetime, timezone

import pytest

from app.domain.contracts import Right
from app.domain.venue import Venue
from app.models import Group, OptionContract  # noqa: F401  (schema registration)

NOW = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)
EXPIRY = date(2026, 12, 18)

needs_postgres = pytest.mark.skipif(
    "postgresql" not in os.getenv("SKEWER_TEST_DATABASE_URL", ""),
    reason="Upserting contracts needs PostgreSQL (ON CONFLICT); set SKEWER_TEST_DATABASE_URL",
)


class _Raw:
    """The shape ``upsert_many`` reads: a ref plus the per-quote extras."""

    def __init__(self, ref, version_nr=None, generation_nr=None, original_strike=None):
        self.ref = ref
        self.contract_size = 100
        self.currency = "EUR"
        self.version_nr = version_nr
        self.generation_nr = generation_nr
        self.original_strike = original_strike


def _ref(isin: str, strike: float, right: Right = Right.CALL):
    from app.domain.contracts import ContractRef

    return ContractRef(
        symbol=f"EUX:{isin}", venue=Venue.EUREX, root="SAP",
        expiration=EXPIRY, right=right, strike=strike, currency="EUR",
    )


@needs_postgres
async def test_an_adjusted_contract_links_to_the_one_it_replaced(db):
    from app.repositories.contract_repo import ContractRepository
    from app.repositories.underlying_repo import UnderlyingRepository

    underlying = await UnderlyingRepository(db).create("SAP.DE", "SAP", venue=Venue.EUREX)
    repo = ContractRepository(db)

    # First poll: the contract as originally listed.
    await repo.upsert_many(
        underlying.id,
        [_Raw(_ref("DE000AAAAAA1", 92.0), version_nr=0, generation_nr=7)],
        seen_at=NOW,
    )

    # After the corporate action: a new ISIN, an adjusted strike, and the
    # product moved on to generation 8 for everything newly listed.
    ids = await repo.upsert_many(
        underlying.id,
        [
            _Raw(_ref("DE000AAAAAA1", 92.0), version_nr=0, generation_nr=7),
            _Raw(_ref("DE000BBBBBB2", 91.49), version_nr=1, generation_nr=7,
                 original_strike=92.0),
            # A fresh, unadjusted contract at the round strike. This is the
            # row that makes matching on original strike alone ambiguous.
            _Raw(_ref("DE000CCCCCC3", 92.0), version_nr=0, generation_nr=8),
        ],
        seen_at=NOW,
    )

    adjusted = await db.get(OptionContract, ids["EUX:DE000BBBBBB2"])
    assert adjusted.supersedes_contract_id == ids["EUX:DE000AAAAAA1"]

    # And the decoy is not touched in either direction.
    decoy = await db.get(OptionContract, ids["EUX:DE000CCCCCC3"])
    assert decoy.supersedes_contract_id is None


@needs_postgres
async def test_an_ambiguous_match_leaves_the_link_null(db):
    """Two candidates is not a reason to pick one. NULL reads as no claim."""
    from app.repositories.contract_repo import ContractRepository
    from app.repositories.underlying_repo import UnderlyingRepository

    underlying = await UnderlyingRepository(db).create("SAP.DE", "SAP", venue=Venue.EUREX)
    repo = ContractRepository(db)

    ids = await repo.upsert_many(
        underlying.id,
        [
            _Raw(_ref("DE000AAAAAA1", 92.0), version_nr=0, generation_nr=7),
            _Raw(_ref("DE000DDDDDD4", 92.0), version_nr=0, generation_nr=7),
            _Raw(_ref("DE000BBBBBB2", 91.49), version_nr=1, generation_nr=7,
                 original_strike=92.0),
        ],
        seen_at=NOW,
    )

    adjusted = await db.get(OptionContract, ids["EUX:DE000BBBBBB2"])
    assert adjusted.supersedes_contract_id is None


@needs_postgres
async def test_an_unadjusted_chain_writes_no_links(db):
    """The common case, and the one that must stay cheap: BMW, IFX and the
    EURO STOXX 50 are entirely at version 0."""
    from app.repositories.contract_repo import ContractRepository
    from app.repositories.underlying_repo import UnderlyingRepository

    underlying = await UnderlyingRepository(db).create("BMW.DE", "BMW", venue=Venue.EUREX)
    repo = ContractRepository(db)

    ids = await repo.upsert_many(
        underlying.id,
        [
            _Raw(_ref("DE000EEEEEE5", 60.0), version_nr=0, generation_nr=5),
            _Raw(_ref("DE000FFFFFF6", 62.0), version_nr=0, generation_nr=5),
        ],
        seen_at=NOW,
    )

    for symbol in ids:
        row = await db.get(OptionContract, ids[symbol])
        assert row.supersedes_contract_id is None
        assert row.version_nr == 0


@needs_postgres
async def test_an_occ_contract_stores_no_version_at_all(db):
    """OCC publishes none of this, and NULL is the honest value for it."""
    from app.repositories.contract_repo import ContractRepository
    from app.repositories.underlying_repo import UnderlyingRepository
    from tests.factories import make_chain

    underlying = await UnderlyingRepository(db).create("NVDA", "NVIDIA")
    chain = make_chain()

    ids = await ContractRepository(db).upsert_many(underlying.id, chain.quotes, seen_at=NOW)

    row = await db.get(OptionContract, next(iter(ids.values())))
    assert row.version_nr is None
    assert row.generation_nr is None
    assert row.original_strike is None


@needs_postgres
async def test_the_series_read_reports_the_break_instead_of_stitching(db):
    from app.repositories.contract_repo import ContractRepository
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services.chain_service import contract_series

    underlying = await UnderlyingRepository(db).create("SAP.DE", "SAP", venue=Venue.EUREX)
    await ContractRepository(db).upsert_many(
        underlying.id,
        [
            _Raw(_ref("DE000AAAAAA1", 92.0), version_nr=0, generation_nr=7),
            _Raw(_ref("DE000BBBBBB2", 91.49), version_nr=1, generation_nr=7,
                 original_strike=92.0),
        ],
        seen_at=NOW,
    )

    new = await contract_series(db, "EUX:DE000BBBBBB2", days=5)
    assert new["version_nr"] == 1
    assert new["original_strike"] == 92.0
    assert new["supersedes"] == "EUX:DE000AAAAAA1"
    assert new["superseded_by"] is None

    old = await contract_series(db, "EUX:DE000AAAAAA1", days=5)
    assert old["superseded_by"] == "EUX:DE000BBBBBB2"
    assert old["supersedes"] is None
