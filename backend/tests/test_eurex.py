"""Eurex identity, feed mapping, and the settlement-as-mark rule.

No network. The feed rows below are copied verbatim from live responses so
that a schema change on Deutsche Boerse's side shows up as a failing mapping
test rather than as a silently empty chain in production.
"""

from datetime import date, datetime, timezone

import numpy as np
import pytest

from app.domain import eurex
from app.domain.contracts import ContractRef, Right
from app.domain.venue import ExerciseStyle, Venue, spec_for
from app.services.compute.bs import CALL, PUT
from app.services.compute.iv import implied_vol, mid_price
from app.services.option_providers.eurex import EurexOptionProvider

# A real row from Contracts(filter:{Product:{eq:"BMW"}}).
BMW_ROW = {
    "ContractID": 436362100,
    "ISIN": "DE000F2P1J24",
    "Product": "BMW",
    "CallPut": "C",
    "Strike": 36,
    "ExerciseStyle": "AMERICAN",
    "SettlementType": "PHYSICAL",
    "ContractSize": 100,
    "VersionNr": 0,
    "ExpirationDate": "2028-06-16",
    "LastTradingDate": "2028-06-16",
    "PreviousDaySettlementPrice": 28.17,
}


# --------------------------------------------------------------- identity

def test_eurex_key_is_namespaced_isin():
    assert eurex.key_for("DE000F2P1J24") == "EUX:DE000F2P1J24"
    assert eurex.isin_of("EUX:DE000F2P1J24") == "DE000F2P1J24"
    assert eurex.is_eurex_key("EUX:DE000F2P1J24")


def test_eurex_and_occ_namespaces_cannot_collide():
    """The whole point of the EUX: prefix.

    Both venues share one unique index on ``option_contracts.symbol``, so a
    collision would be silent data corruption rather than an error.
    """
    occ = ContractRef.parse("NVDA260821C00002500")
    eux = eurex.contract_ref(
        isin="DE000F2P1J24", product="BMW", expiration=date(2028, 6, 16),
        right=Right.CALL, strike=36.0,
    )
    assert not eurex.is_eurex_key(occ.symbol)
    assert eurex.is_eurex_key(eux.symbol)
    assert occ.symbol != eux.symbol


@pytest.mark.parametrize("bad", ["", "TOOSHORT", "DE000F2P1J24X", "DE000F2P1J2!"])
def test_malformed_isin_is_rejected(bad):
    with pytest.raises(eurex.EurexParseError):
        eurex.key_for(bad)


def test_right_and_exercise_mapping():
    assert eurex.right_from_callput("C") is Right.CALL
    assert eurex.right_from_callput("P") is Right.PUT
    with pytest.raises(eurex.EurexParseError):
        eurex.right_from_callput("X")

    assert eurex.exercise_from_feed("AMERICAN") is ExerciseStyle.AMERICAN
    assert eurex.exercise_from_feed("EUROPEAN") is ExerciseStyle.EUROPEAN
    # Unknown falls back rather than raising: an unrecognised style is a
    # reason to keep collecting and flag it, not to drop the contract.
    assert eurex.exercise_from_feed("BERMUDAN") is ExerciseStyle.AMERICAN
    assert eurex.exercise_from_feed(None) is ExerciseStyle.AMERICAN


# ------------------------------------------------------------------ venue

def test_expiry_instant_is_venue_dependent():
    """The bug this prevents is a whole trading day of tenor error at 0DTE."""
    occ = ContractRef.parse("NVDA260821C00002500")
    eux = eurex.contract_ref(
        isin="DE000F2P1J24", product="BMW", expiration=date(2026, 8, 21),
        right=Right.CALL, strike=36.0,
    )
    assert occ.expires_at() == datetime(
        2026, 8, 21, 16, 0, tzinfo=spec_for(Venue.OCC).expiry_tz
    )
    assert eux.expires_at() == datetime(
        2026, 8, 21, 17, 30, tzinfo=spec_for(Venue.EUREX).expiry_tz
    )
    # 17:30 Berlin is 11:30 New York, so the Eurex contract dies first.
    assert eux.expires_at() < occ.expires_at()


def test_occ_refs_carry_their_venue_defaults():
    ref = ContractRef.parse("NVDA260821C00002500")
    assert ref.venue is Venue.OCC
    assert ref.currency == "USD"
    assert ref.contract_size == 100
    # US listed equity options are American-style, and saying so explicitly is
    # the point of recording it.
    assert ref.exercise_style is ExerciseStyle.AMERICAN


# ------------------------------------------------------------ feed mapping

def test_contract_row_maps_to_a_quote():
    provider = EurexOptionProvider()
    quote = provider._to_quote(BMW_ROW, {436362100: 27.12}, "EUR", 100)

    assert quote is not None
    assert quote.ref.symbol == "EUX:DE000F2P1J24"
    assert quote.ref.venue is Venue.EUREX
    assert quote.ref.right is Right.CALL
    assert quote.ref.strike == 36.0
    assert quote.ref.expiration == date(2028, 6, 16)
    assert quote.ref.exercise_style is ExerciseStyle.AMERICAN
    assert quote.ref.contract_size == 100
    assert quote.ref.currency == "EUR"
    assert quote.settlement == 27.12


def test_feed_has_no_order_book_so_quote_fields_are_null():
    """NULL because the field does not exist, not because the book was empty.

    Anything that later reads a bid of 0.0 here would be reading an invention.
    """
    provider = EurexOptionProvider()
    quote = provider._to_quote(BMW_ROW, {}, "EUR", 100)
    assert quote.bid is None
    assert quote.ask is None
    assert quote.last is None
    # Open interest is a fee-based Eurex file, not part of the free API. NULL,
    # not 0, or every European contract would read as having no open interest.
    assert quote.open_interest is None
    assert quote.iv_provider is None


def test_previous_day_settlement_is_only_a_fallback():
    provider = EurexOptionProvider()
    today = provider._to_quote(BMW_ROW, {436362100: 27.12}, "EUR", 100)
    stale = provider._to_quote(BMW_ROW, {}, "EUR", 100)
    assert today.settlement == 27.12
    assert stale.settlement == 28.17  # PreviousDaySettlementPrice


@pytest.mark.parametrize(
    "row",
    [
        {**BMW_ROW, "ISIN": None},
        {**BMW_ROW, "ExpirationDate": None},
        {**BMW_ROW, "Strike": None},
        {**BMW_ROW, "CallPut": "X"},
        {**BMW_ROW, "ISIN": "NOTANISIN"},
    ],
)
def test_unusable_rows_are_dropped_not_guessed(row):
    assert EurexOptionProvider()._to_quote(row, {}, "EUR", 100) is None


def test_product_code_resolution():
    p = EurexOptionProvider
    assert p.product_for("BMW.DE") == "BMW"
    assert p.product_for("BMW") == "BMW"
    # An explicit product always wins, because stripping the suffix is only a
    # heuristic — Euronext's Ahold is "AH" against a ticker of "AD.AS".
    assert p.product_for("AD.AS", "AH") == "AH"


# ------------------------------------------------- settlement as a mark

def test_settlement_becomes_the_mark_when_there_is_no_book():
    """The rule that makes a European chain priceable at all.

    Distinct from the zero-bid rule: a 0.0 bid is the absence of a quote and
    must never become a mark, while a settlement price is the exchange's own
    valuation and is the most authoritative number of the session.
    """
    bid = np.array([np.nan, 1.0])
    ask = np.array([np.nan, 1.2])
    settlement = np.array([2.96, 9.99])

    mark = mid_price(bid, ask)
    mark = np.where(np.isfinite(mark), mark, settlement)

    assert mark[0] == 2.96              # no book -> settlement
    assert mark[1] == pytest.approx(1.1)  # real two-sided quote wins


def test_settlement_priced_chain_solves_for_iv():
    """An end-to-end sanity check on the numbers, with no network.

    Values are from BMW's real 2026-09-07 session: spot 62.80, a 64-strike put
    settling at 2.96. If this stops implying a plausible vol, the units are
    wrong somewhere.
    """
    now = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    ref = eurex.contract_ref(
        isin="DE000F376HK3", product="BMW", expiration=date(2026, 10, 9),
        right=Right.PUT, strike=64.0,
    )
    res = implied_vol(
        np.array([2.96]), 62.80, np.array([64.0]),
        np.array([ref.year_fraction(now)]), 0.04, 0.0701,
        np.array([PUT]),
    )
    assert res.status[0] == "ok"
    assert 0.10 < res.iv[0] < 1.00


def test_no_settlement_and_no_book_implies_nothing():
    """A contract the feed did not settle has no price, and therefore no IV."""
    mark = mid_price(np.array([np.nan]), np.array([np.nan]))
    settlement = np.array([np.nan])
    mark = np.where(np.isfinite(mark), mark, settlement)
    res = implied_vol(
        mark, 62.80, np.array([64.0]), np.array([0.08]), 0.04, 0.0701, np.array([CALL])
    )
    assert not np.isfinite(res.iv[0])
    assert res.status[0] == "no_quote"
