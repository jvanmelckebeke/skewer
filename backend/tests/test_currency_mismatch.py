"""A chain whose strikes and spot are in different currencies is not priced.

Eurex composes two feeds. Deutsche Boerse publishes the option product and its
currency; Yahoo supplies the spot. Nothing makes them agree, and on COPA they
do not: the product is an option on WisdomTree Copper settling in USD, while
Yahoo's COPA.MI is the Milan listing in EUR. At ~1.17 EUR/USD the chain solved
to an ATM volatility of 300%, which is the failure this codebase cares most
about. A plausible number that is wrong is worse than no number.
"""

import os
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.models import Group  # noqa: F401  (schema registration; see conftest)
from app.services.chain_sync import _currency_mismatch
from app.services.compute.iv import IVStatus
from tests.factories import make_chain

NOW = datetime(2026, 9, 9, 14, 35, tzinfo=timezone.utc)


def _mismatched():
    """Options in USD, spot in EUR. COPA, in miniature."""
    return replace(make_chain(today=NOW.date()), currency="USD", spot_currency="EUR")


def test_matching_currencies_are_not_a_mismatch():
    assert not _currency_mismatch(replace(make_chain(), currency="EUR", spot_currency="eur"))


def test_differing_currencies_are_a_mismatch():
    assert _currency_mismatch(_mismatched())


def test_an_unknown_spot_currency_is_read_as_agreement():
    """Refusing on "unknown" would unprice every venue that cannot answer,
    which is a worse failure than the one being guarded against."""
    assert not _currency_mismatch(replace(make_chain(), currency="USD", spot_currency=None))


needs_postgres = pytest.mark.skipif(
    "postgresql" not in os.getenv("SKEWER_TEST_DATABASE_URL", ""),
    reason="Persisting a chain needs PostgreSQL (ON CONFLICT); set SKEWER_TEST_DATABASE_URL",
)


@needs_postgres
async def test_a_mismatched_chain_is_stored_unpriced_with_a_reason(db):
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services.chain_sync import persist_chain

    underlying = await UnderlyingRepository(db).create("COPA.MI", "Copper")

    result = await persist_chain(db, underlying.id, _mismatched(), full=True, now=NOW)

    # The quotes are the market's own prices and are not recoverable later, so
    # they are kept. Only the derived numbers are refused.
    assert result.persisted == len(_mismatched().quotes)
    assert result.iv_solved == 0

    from sqlalchemy import text
    rows = await db.execute(
        text(
            "select distinct q.iv_status, count(q.iv) from option_quotes q"
            " join option_contracts c on c.id = q.contract_id"
            " where c.underlying_id = :uid group by q.iv_status"
        ),
        {"uid": underlying.id},
    )
    assert rows.all() == [(IVStatus.FX_MISMATCH, 0)]


@needs_postgres
async def test_a_mismatched_chain_does_not_move_the_dividend_yield(db):
    """Parity off a mismatched chain is nonsense too, and it is written back
    to the underlying where the read path prices every greek against it."""
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services.chain_sync import persist_chain

    repo = UnderlyingRepository(db)
    underlying = await repo.create("COPA.MI", "Copper")

    await persist_chain(db, underlying.id, _mismatched(), full=True, now=NOW)

    refreshed = await repo.get_by_symbol("COPA.MI")
    assert float(refreshed.dividend_yield or 0.0) == 0.0


@needs_postgres
async def test_the_same_chain_in_one_currency_still_prices(db):
    """The control. Without it the test above passes on a broken pipeline."""
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services.chain_sync import persist_chain

    underlying = await UnderlyingRepository(db).create("COPA.MI", "Copper")
    matched = replace(make_chain(today=NOW.date()), currency="USD", spot_currency="USD")

    result = await persist_chain(db, underlying.id, matched, full=True, now=NOW)

    assert result.iv_solved > 0
