"""Euronext identity and EWS response mapping.

No network, and no auth key — the API has neither an anonymous mode nor a
sandbox, so nothing here has been checked against a live response. The fixture
below is assembled from the sample values in Annex A of the v1.4 derivatives
specification, which makes these tests a check that the mapping matches the
*document*. They will not catch the document being wrong.

The one assumption worth calling out is the price denominators. The spec
publishes `instrumentDenominator` and `strikePriceDenominator` without ever
saying in prose that prices are divided by them. `test_denominator_mismatch_is
_rejected` is the tripwire: the identifier carries the true strike, so if the
scaling is backwards the cross-check fails loudly rather than writing a chain
that is off by a factor of 100.
"""

from datetime import date

import pytest

from app.domain import euronext
from app.domain.contracts import ContractRef, Right
from app.domain.venue import ExerciseStyle, Venue
from app.services.option_providers.euronext import EuronextOptionProvider

# Shaped from Annex A samples. A Philips December 2026 34.00 call on Amsterdam.
# strikePriceDenominator 100 turns strikePx "3400.0" into 34.00;
# instrumentDenominator 10 turns the book's "12.5" into 1.25.
PHIA_CALL = {
    "sessionQuality": "DT",
    "instr": {
        "iid": "14102444",
        "exchCode": "DAMS",
        "cdStand": "PHIA-C-202612-34.00",
        "codifStand": "DER",
        "cfiCode": "OCASPS",
        "type": "OPT",
        "currency": "EUR",
        "exchEndDt": "20261218",
        "strikePx": "3400.0",
        "warType": "C",
        "accuracy": "2",
        "specFeat": [
            {"field": "listed", "fieldType": "Integer", "value": "2"},
            {"field": "instrumentDenominator", "fieldType": "Integer", "value": "10"},
            {"field": "strikePriceDenominator", "fieldType": "Integer", "value": "100"},
        ],
        "currInstrSess": {
            "sessionType": "CUR",
            "lastPx": "13.0",
            "tradedQty": "460.0",
            "nbTrades": "15",
            "lastUpdate": "20261118-12:16:27",
            "openInt": "2482.0",
            "openIntDateTime": "20261117-00:00:00",
        },
        "prevInstrSess": {
            "sessionType": "DCC",
            "closPx": "12.8",
            "setlmtPx": "12.9",
            "setlmtPxDtTm": "20261117-17:39:25",
            "openInt": "2368.0",
        },
        "ordBook": {
            "bsBidPx": "12.5",
            "bsBidQty": "30.0",
            "bsBidDtTm": "20261118-12:16:26",
            "bsAskPx": "13.5",
            "bsAskQty": "4.0",
            "bsAskDtTm": "20261118-12:16:26",
        },
    },
}


# --------------------------------------------------------------- identity

def test_identifier_round_trips():
    identifier = euronext.instrument_code("PHIA", Right.CALL, (2026, 12), 34.0)
    assert identifier == "PHIA-C-202612-34.00"
    parsed = euronext.parse_identifier(identifier)
    assert parsed["code"] == "PHIA"
    assert parsed["right"] is Right.CALL
    assert (parsed["year"], parsed["month"]) == (2026, 12)
    assert parsed["strike"] == 34.0


def test_index_style_integer_strikes():
    """A four-figure index strike is normally quoted without decimals."""
    assert euronext.instrument_code(
        "AEX", Right.PUT, (2027, 3), 900.0, strike_decimals=0
    ) == "AEX-P-202703-900"


def test_key_carries_the_exchange():
    """The same code can list on more than one Euronext market."""
    ams = euronext.key_for("PHIA-C-202612-34.00", "DAMS")
    bru = euronext.key_for("PHIA-C-202612-34.00", "DBRU")
    assert ams != bru
    assert euronext.split_key(ams) == ("DAMS", "PHIA-C-202612-34.00")


def test_unknown_exchange_is_rejected():
    with pytest.raises(euronext.EuronextParseError):
        euronext.key_for("PHIA-C-202612-34.00", "XXXX")


def test_three_venue_namespaces_cannot_collide():
    """All three share one unique index on option_contracts.symbol."""
    from app.domain import eurex

    occ = ContractRef.parse("NVDA260821C00002500").symbol
    eux = eurex.contract_ref(
        isin="DE000F2P1J24", product="BMW", expiration=date(2028, 6, 16),
        right=Right.CALL, strike=36.0,
    ).symbol
    xnt = euronext.key_for("PHIA-C-202612-34.00", "DAMS")
    assert len({occ, eux, xnt}) == 3
    assert not euronext.is_euronext_key(occ)
    assert not euronext.is_euronext_key(eux)
    assert euronext.is_euronext_key(xnt)


@pytest.mark.parametrize(
    "bad",
    ["PHIA-X-202612-34.00", "PHIA-C-2026-34.00", "PHIA-C-202613-34.00",
     "PHIA-C-202612", "-C-202612-34.00"],
)
def test_malformed_identifiers_are_rejected(bad):
    with pytest.raises(euronext.EuronextParseError):
        euronext.parse_identifier(bad)


def test_expiration_must_agree_with_the_identifier_month():
    """The identifier carries only YYYYMM; the real date comes from exchEndDt.

    A date from a different month means the two disagree about which contract
    this is, which is a fault rather than something to reconcile.
    """
    with pytest.raises(euronext.EuronextParseError):
        euronext.contract_ref(
            identifier="PHIA-C-202612-34.00", expiration=date(2027, 1, 15)
        )
    ref = euronext.contract_ref(
        identifier="PHIA-C-202612-34.00", expiration=date(2026, 12, 18)
    )
    # The day is not derivable from the identifier, so it must come from the
    # feed rather than being guessed at the third Friday.
    assert ref.expiration == date(2026, 12, 18)


def test_exercise_style_comes_from_the_cfi_code():
    assert euronext.exercise_from_cfi("OCASPS") is ExerciseStyle.AMERICAN
    assert euronext.exercise_from_cfi("OPEXXX") is ExerciseStyle.EUROPEAN
    # Bermudan has no model here, so it reads as unknown rather than being
    # quietly rounded to American.
    assert euronext.exercise_from_cfi("OCBSPS") is None
    assert euronext.exercise_from_cfi("FFICSX") is None
    assert euronext.exercise_from_cfi(None) is None


def test_venue_expiry_instant():
    ref = euronext.contract_ref(
        identifier="PHIA-C-202612-34.00", expiration=date(2026, 12, 18)
    )
    assert ref.venue is Venue.EURONEXT
    assert ref.expires_at().hour == 17
    assert ref.expires_at().minute == 30
    assert str(ref.expires_at().tzinfo) == "Europe/Amsterdam"


# ------------------------------------------------------------ feed mapping

def test_response_maps_to_a_quote_with_denominators_applied():
    quote = EuronextOptionProvider()._to_quote(
        PHIA_CALL, "PHIA-C-202612-34.00", "DAMS"
    )
    assert quote is not None
    assert quote.ref.symbol == "XNT:DAMS:PHIA-C-202612-34.00"
    assert quote.ref.strike == 34.0
    assert quote.ref.right is Right.CALL
    assert quote.ref.expiration == date(2026, 12, 18)
    assert quote.ref.exercise_style is ExerciseStyle.AMERICAN
    assert quote.ref.currency == "EUR"
    # instrumentDenominator is 10, so the book's "12.5"/"13.5" scale down.
    assert quote.bid == 1.25
    assert quote.ask == 1.35
    assert quote.last == 1.30
    assert quote.settlement == 1.29
    assert quote.open_interest == 2482
    assert quote.volume == 460


def test_unlike_eurex_there_is_a_real_book():
    """So the mark is a genuine mid, not a settlement fallback."""
    quote = EuronextOptionProvider()._to_quote(
        PHIA_CALL, "PHIA-C-202612-34.00", "DAMS"
    )
    assert quote.bid is not None and quote.ask is not None
    assert quote.ask > quote.bid


def test_timestamps_are_localised_to_the_venue_not_utc():
    quote = EuronextOptionProvider()._to_quote(
        PHIA_CALL, "PHIA-C-202612-34.00", "DAMS"
    )
    stamp = quote.last_trade_at
    assert stamp is not None
    assert stamp.hour == 12 and stamp.minute == 16
    # Reading a Central European stamp as UTC would move it two hours.
    assert stamp.utcoffset().total_seconds() != 0


def test_denominator_mismatch_is_rejected():
    """The tripwire for the one genuinely unverified assumption.

    If `strikePriceDenominator` is applied the wrong way round, the scaled
    strike stops matching the identifier and the row is dropped rather than
    written 100x wrong.
    """
    broken = {
        **PHIA_CALL,
        "instr": {
            **PHIA_CALL["instr"],
            "specFeat": [
                {"field": "strikePriceDenominator", "value": "1"},
                {"field": "instrumentDenominator", "value": "10"},
            ],
        },
    }
    assert EuronextOptionProvider()._to_quote(
        broken, "PHIA-C-202612-34.00", "DAMS"
    ) is None


def test_missing_expiry_drops_the_contract():
    """No exchEndDt means no tenor, and no tenor means nothing is priceable."""
    broken = {**PHIA_CALL, "instr": {**PHIA_CALL["instr"], "exchEndDt": None}}
    assert EuronextOptionProvider()._to_quote(
        broken, "PHIA-C-202612-34.00", "DAMS"
    ) is None


def test_open_interest_falls_back_to_the_previous_session():
    """OI is struck overnight, so an early poll may not have today's yet."""
    no_current = {
        **PHIA_CALL,
        "instr": {
            **PHIA_CALL["instr"],
            "currInstrSess": {k: v for k, v in PHIA_CALL["instr"]["currInstrSess"].items()
                              if k != "openInt"},
        },
    }
    quote = EuronextOptionProvider()._to_quote(
        no_current, "PHIA-C-202612-34.00", "DAMS"
    )
    assert quote.open_interest == 2368


# ------------------------------------------------------------------- grid

def test_product_resolution_and_exchange_override():
    p = EuronextOptionProvider
    assert p.product_for("PHIA.AS") == ("DAMS", "PHIA")
    # Amsterdam is only a default; Brussels names must say so.
    assert p.product_for("ABI.BR", "DBRU:ABI") == ("DBRU", "ABI")
    assert p.product_for("AEX", "AEX") == ("DAMS", "AEX")


def test_strike_grid_brackets_spot():
    provider = EuronextOptionProvider()
    strikes = provider._strikes(34.0, provider.strike_step_for(34.0))
    assert strikes[0] < 34.0 < strikes[-1]
    # Step is 1.0 in the 10-50 band, and the window is +/-30% by default.
    assert all(abs(b - a - 1.0) < 1e-9 for a, b in zip(strikes, strikes[1:]))


def test_strike_step_scales_with_price_level():
    p = EuronextOptionProvider
    assert p.strike_step_for(5.0) < p.strike_step_for(34.0)
    assert p.strike_step_for(34.0) < p.strike_step_for(900.0)


def test_maturities_roll_across_the_year_boundary():
    provider = EuronextOptionProvider()
    out = provider._maturities(date(2026, 11, 3))
    assert out[0] == (2026, 11)
    assert (2027, 1) in out


# ------------------------------------------------------------------- auth

@pytest.mark.asyncio
async def test_missing_auth_key_refuses_rather_than_401ing():
    """There is no anonymous mode, so an unset key is a config error.

    Failing here keeps a missing key from becoming several hundred 401s, and
    lets `add_underlying` reject the ticker instead of accepting one that
    would silently never collect.
    """
    from app.services.option_providers.base import ChainUnavailable

    provider = EuronextOptionProvider()
    provider._auth_key = ""
    assert provider.configured is False
    with pytest.raises(ChainUnavailable, match="auth key"):
        await provider.fetch_chain("PHIA.AS")
    assert await provider.validate("PHIA.AS") == (False, "")
