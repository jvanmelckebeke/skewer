"""OCC symbol parsing — the identity layer everything else keys on."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.domain.occ import ContractRef, OccParseError, Right


def test_parses_a_real_symbol():
    ref = ContractRef.parse("NVDA260821C00002500")
    assert ref.root == "NVDA"
    assert ref.expiration == date(2026, 8, 21)
    assert ref.right is Right.CALL
    assert ref.strike == 2.5


def test_parses_puts_and_large_strikes():
    ref = ContractRef.parse("SPY271217P00450000")
    assert ref.right is Right.PUT
    assert ref.strike == 450.0
    assert ref.expiration == date(2027, 12, 17)


@pytest.mark.parametrize("occ", [
    "NVDA260821C0000250",     # strike too short
    "NVDA2608X1C00002500",    # non-numeric date
    "NVDA269921C00002500",    # impossible month
    "C00002500",              # no root
    "",
])
def test_rejects_malformed(occ):
    with pytest.raises(OccParseError):
        ContractRef.parse(occ)


def test_build_round_trips():
    for occ in ("NVDA260821C00002500", "SPY271217P00450000", "AMD280121C00123500"):
        ref = ContractRef.parse(occ)
        assert ContractRef.build(ref.root, ref.expiration, ref.right, ref.strike).symbol == occ


def test_expiry_is_the_market_close_not_midnight():
    """0DTE greeks are entirely determined by this."""
    ref = ContractRef.parse("NVDA260821C00200000")
    assert ref.expires_at() == datetime(2026, 8, 21, 16, 0, tzinfo=ZoneInfo("America/New_York"))


def test_year_fraction_floors_at_zero_for_expired():
    ref = ContractRef.parse("NVDA260821C00200000")
    after = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    assert ref.year_fraction(after) == 0.0


def test_year_fraction_is_act_365():
    ref = ContractRef.parse("NVDA270821C00200000")
    now = datetime(2026, 8, 21, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    assert ref.year_fraction(now) == pytest.approx(365 / 365.0, rel=1e-3)


def test_matches_detects_provider_metadata_drift():
    ref = ContractRef.parse("NVDA260821C00002500")
    assert ref.matches(2.5, date(2026, 8, 21))
    assert not ref.matches(2.6, date(2026, 8, 21))
    assert not ref.matches(2.5, date(2026, 8, 22))
    assert ref.matches(None, None)


def test_dte():
    ref = ContractRef.parse("NVDA260821C00002500")
    assert ref.dte(date(2026, 8, 21)) == 0
    assert ref.dte(date(2026, 8, 14)) == 7
    assert ref.dte(date(2026, 8, 22)) == -1
