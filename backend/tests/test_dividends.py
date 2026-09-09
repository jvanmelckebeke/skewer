"""The dividend yield read off put-call parity.

The round trip is the test that matters: price a synthetic European chain at a
known q, then check the measurement recovers it. Everything else here is a
guard, and every guard exists because the alternative is a plausible-looking
number rather than a missing one.
"""

import os
from dataclasses import replace
from datetime import date, datetime, timezone

import numpy as np
import pytest

from app.domain.occ import Right
from app.services import underlying_service
from app.services.compute.bs import CALL, PUT, price
from app.services.compute.dividends import (
    MAX_YIELD,
    MIN_TENOR_YEARS,
    implied_yield_by_expiry,
    representative_yield,
    yield_per_contract,
)
from tests.factories import make_chain

SPOT = 100.0
RATE = 0.04


def _chain(q: float, tenors=(0.25, 0.5), strikes=(96.0, 98.0, 100.0, 102.0, 104.0),
           sigma: float = 0.30):
    """A European chain priced at a known yield, calls and puts at every strike."""
    strike, tenor, is_call, mark = [], [], [], []
    for t in tenors:
        for k in strikes:
            for call in (True, False):
                strike.append(k)
                tenor.append(t)
                is_call.append(call)
                mark.append(
                    float(price(SPOT, k, t, sigma, RATE, q, CALL if call else PUT))
                )
    return (
        np.array(strike), np.array(tenor),
        np.array(is_call, dtype=bool), np.array(mark),
    )


@pytest.mark.parametrize("q", [0.0, 0.03, 0.07, -0.02])
def test_the_yield_a_chain_was_priced_at_is_recovered(q):
    """Parity is an identity on European prices, so this should be exact."""
    by_expiry = implied_yield_by_expiry(SPOT, *_chain(q), RATE)

    assert len(by_expiry) == 2
    for measured in by_expiry.values():
        assert measured == pytest.approx(q, abs=1e-9)


def test_the_measurement_is_independent_of_volatility():
    """Parity contains no sigma. If it did, this would be circular: the yield
    would depend on the IV solved against it."""
    low = implied_yield_by_expiry(SPOT, *_chain(0.05, sigma=0.10), RATE)
    high = implied_yield_by_expiry(SPOT, *_chain(0.05, sigma=0.90), RATE)

    assert list(low.values()) == pytest.approx(list(high.values()), abs=1e-9)


def test_a_short_tenor_is_refused_rather_than_amplified():
    """ln(F/S)/T divides by the tenor, so a one-cent error at 1DTE becomes tens
    of percent. Below the floor there is no measurement to make."""
    short = MIN_TENOR_YEARS / 2

    assert implied_yield_by_expiry(SPOT, *_chain(0.03, tenors=(short,)), RATE) == {}


def test_too_few_pairs_is_refused():
    """One pair is an anecdote — a single stale mid moves it without a trace."""
    thin = implied_yield_by_expiry(SPOT, *_chain(0.03, strikes=(100.0, 102.0)), RATE)

    assert thin == {}


def test_an_implausible_result_is_dropped_not_clamped():
    """The same rule as the sigma bracket: an answer outside the band is
    evidence the inputs were bad, and returning the edge would manufacture the
    artefact the band exists to catch.

    Worth being clear about what this does and does not catch, because the
    band turns out to be the tenor floor viewed from a second angle. At three
    months, halving every call price still lands on a plausible-looking 15%
    and even taking calls to a tenth only reaches 25%: the same absolute
    parity error simply cannot express itself as a large yield when divided
    by a long tenor. Just above the floor it can, which is the whole reason
    the floor is there. So the band is a backstop against short-tenor
    amplification, and near-the-money pairs plus a median across strikes are
    what actually defend the measurement."""
    strike, tenor, is_call, mark = _chain(0.03, tenors=(MIN_TENOR_YEARS * 1.05,))
    # Ten cents on the dollar for every call, a fortnight from expiry.
    mark = np.where(is_call, mark / 10, mark)

    out = implied_yield_by_expiry(SPOT, strike, tenor, is_call, mark, RATE)

    assert MAX_YIELD not in out.values()
    assert out == {}


def test_strikes_far_from_the_money_are_not_used():
    """Deep in-the-money marks are nearly all intrinsic, with the widest
    relative spreads and the largest early-exercise premium on an American
    contract. Both arguments point at the same window."""
    wide = implied_yield_by_expiry(
        SPOT, *_chain(0.03, strikes=(40.0, 60.0, 160.0, 180.0, 200.0)), RATE
    )

    assert wide == {}


def test_a_missing_expiry_falls_back_per_contract():
    tenor = np.array([0.25, 0.25, 2.0])

    out = yield_per_contract({0.25: 0.06}, tenor, fallback=0.01)

    assert out.tolist() == [0.06, 0.06, 0.01]


def test_the_representative_yield_is_the_median_across_expiries():
    """Not the front one: it is both the noisiest and the least representative,
    since it may sit entirely before or after the next ex-date."""
    assert representative_yield({0.1: 0.01, 0.5: 0.05, 1.0: 0.90}) == 0.05
    assert representative_yield({}) is None


needs_postgres = pytest.mark.skipif(
    "postgresql" not in os.getenv("SKEWER_TEST_DATABASE_URL", ""),
    reason="Persisting a chain needs PostgreSQL (ON CONFLICT); set SKEWER_TEST_DATABASE_URL",
)

TODAY = date(2026, 8, 21)
NOW = datetime(2026, 8, 21, 14, 35, tzinfo=timezone.utc)


async def _priced_chain(symbol: str, q: float):
    """A synthetic chain whose marks are BSM prices at a known yield.

    ``make_chain``'s own marks are a rough intrinsic-plus-time-value fudge, so
    parity does not hold on them. Repricing every quote is what makes the
    round trip through the collector meaningful.
    """
    chain = make_chain(symbol=symbol, today=TODAY, spot=SPOT, dtes=(30, 90, 180))
    quotes = []
    for quote in chain.quotes:
        t = quote.ref.year_fraction(NOW)
        phi = CALL if quote.ref.right is Right.CALL else PUT
        fair = float(price(SPOT, quote.ref.strike, t, 0.30, RATE, q, phi))
        quotes.append(replace(quote, bid=fair - 0.01, ask=fair + 0.01))
    return replace(chain, quotes=quotes)


@needs_postgres
async def test_the_collector_records_the_yield_it_measured(db):
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services.chain_sync import persist_chain

    repo = UnderlyingRepository(db)
    underlying = await repo.create("DIVY", "Divy Inc")
    assert float(underlying.dividend_yield or 0.0) == 0.0

    await persist_chain(db, underlying.id, await _priced_chain("DIVY", 0.05),
                        full=True, now=NOW)

    refreshed = await repo.get_by_symbol("DIVY")
    assert float(refreshed.dividend_yield) == pytest.approx(0.05, abs=1e-6)
    # Still a measurement, so a later poll is free to revise it.
    assert refreshed.dividend_yield_is_manual is False


@needs_postgres
async def test_a_stated_yield_outranks_a_measured_one(db):
    """The provenance rule: a human said 9%, and the collector runs every five
    minutes. It must not win by attrition."""
    from app.repositories.underlying_repo import UnderlyingRepository
    from app.services.chain_sync import persist_chain

    repo = UnderlyingRepository(db)
    underlying = await repo.create("MANU", "Manu Inc")
    await underlying_service.update_underlying(db, "MANU", {"dividend_yield": 0.09})

    await persist_chain(db, underlying.id, await _priced_chain("MANU", 0.05),
                        full=True, now=NOW, imply_dividends=False)

    refreshed = await repo.get_by_symbol("MANU")
    assert float(refreshed.dividend_yield) == pytest.approx(0.09)
    assert refreshed.dividend_yield_is_manual is True
