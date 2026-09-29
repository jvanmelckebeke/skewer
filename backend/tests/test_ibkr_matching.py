"""Matching skewer's Eurex rows to IBKR conIds by their terms.

The shapes are the live ones from the BMW and SAP catalog probes: BMW has a
European twin BMWE on identical terms, and SAP's adjusted series is trading
class ``SAP.1`` with multiplier 101 and a two-decimal strike.
"""

import re
from datetime import date

from app.domain.contracts import Right
from app.services.ibkr.base import ContractInfo
from app.services.ibkr.matching import CatalogRow, match, product_code

OCT = date(2026, 10, 16)
SUFFIX = re.compile(r"\.\d+$")
DEC = date(2026, 12, 18)


def ib(con_id, cls, expiry, strike, right, multiplier="100"):
    return ContractInfo(
        conId=con_id, tradingClass=cls, lastTradeDateOrContractMonth=expiry.strftime("%Y%m%d"),
        strike=strike, right=right, multiplier=multiplier,
    )


def row(row_id, expiry, strike, right, size=100):
    return CatalogRow(id=row_id, expiration=expiry, strike=strike, right=right, contract_size=size)


def test_adjusted_suffix_is_stripped():
    assert product_code("SAP.1", SUFFIX) == "SAP"
    assert product_code("BMW", SUFFIX) == "BMW"
    assert product_code("BMWE", SUFFIX) == "BMWE"


def test_the_european_twin_does_not_collide():
    """BMWE lists the same expiry, strike and right as BMW; the class separates them."""
    contracts = [ib(1, "BMW", OCT, 56.0, "C"), ib(2, "BMWE", OCT, 56.0, "C")]

    result = match("BMW", [row(10, OCT, 56.0, Right.CALL)], contracts, SUFFIX)

    assert result.links == {10: 1}
    assert result.ambiguous == 0


def test_an_adjusted_series_matches_on_class_root_and_multiplier():
    contracts = [
        ib(534012582, "SAP.1", DEC, 91.49, "C", multiplier="101"),
        ib(7, "SAP", DEC, 92.0, "C"),
    ]
    rows = [row(20, DEC, 91.49, Right.CALL, size=101), row(21, DEC, 92.0, Right.CALL)]

    assert match("SAP", rows, contracts, SUFFIX).links == {20: 534012582, 21: 7}


def test_the_multiplier_is_part_of_the_key():
    contracts = [ib(1, "SAP.1", DEC, 91.49, "C", multiplier="101")]

    result = match("SAP", [row(20, DEC, 91.49, Right.CALL, size=100)], contracts, SUFFIX)

    assert result.links == {}
    assert result.unmatched_rows == 1


def test_several_candidates_write_no_link():
    contracts = [ib(1, "BMW", OCT, 56.0, "P"), ib(2, "BMW", OCT, 56.0, "P")]

    result = match("BMW", [row(10, OCT, 56.0, Right.PUT)], contracts, SUFFIX)

    assert result.links == {}
    assert result.ambiguous == 1


def test_strikes_compare_at_four_decimals():
    contracts = [ib(1, "BMW", OCT, 0.01, "C")]  # a LEPO

    assert match("BMW", [row(10, OCT, 0.010000001, Right.CALL)], contracts, SUFFIX).links == {10: 1}


def test_a_strike_listed_by_ibkr_first_is_counted_not_linked():
    """IBKR shows same-day listings a day before Deutsche Boerse's reference data."""
    contracts = [ib(1, "BMW", OCT, 56.0, "C"), ib(2, "BMW", OCT, 45.5, "C")]

    result = match("BMW", [row(10, OCT, 56.0, Right.CALL)], contracts, SUFFIX)

    assert result.links == {10: 1}
    assert result.unmatched_ibkr == 1
