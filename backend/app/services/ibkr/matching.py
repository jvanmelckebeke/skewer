"""Link skewer's Eurex contracts to IBKR conIds by their terms.

IBKR publishes no ISIN for Eurex options, so the ``EUX:`` + ISIN key cannot be
looked up there. The terms can: a contract is the tuple

    (product, expiry, strike, right, contract size)

where the product is IBKR's ``tradingClass`` with any ``.N`` suffix removed
(an adjusted SAP series is class ``SAP.1``, multiplier 101) and must equal the
Eurex product code. The class is what separates Eurex's American ``BMW`` from
its European twin ``BMWE``, which lists the same expiry, strike and right; the
multiplier separates an adjusted series from a standard one.

Measured against the live catalogs: BMW matched 1,048 of 1,048 rows and SAP
1,262 of 1,262, the four adjusted SAP series included. IBKR lists some strikes
a day before Deutsche Boerse's reference data does; those simply stay unmatched
until the Eurex catalog carries them.

Zero candidates or several means no link. NULL and "not matched yet" read the
same way, and a wrong conId would quote the wrong contract into a row's series.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from app.domain.contracts import Right
from app.services.ibkr.base import ContractInfo

_ADJUSTED_SUFFIX = re.compile(r"\.\d+$")

Key = tuple[str, date, float, str, int]


@dataclass(frozen=True, slots=True)
class CatalogRow:
    """The terms of one stored contract, captured while its ORM row is live."""

    id: int
    expiration: date
    strike: float
    right: Right
    contract_size: int


@dataclass(slots=True)
class MatchResult:
    links: dict[int, int] = field(default_factory=dict)
    """Row id to conId, for rows with exactly one candidate on each side."""
    unmatched_rows: int = 0
    ambiguous: int = 0
    unmatched_ibkr: int = 0


def product_code(trading_class: str) -> str:
    """``SAP.1`` -> ``SAP``: the Eurex product an IBKR trading class belongs to."""
    return _ADJUSTED_SUFFIX.sub("", trading_class)


def _ibkr_key(c: ContractInfo) -> Key | None:
    expiry = c.expiry
    try:
        size = int(float(c.multiplier))
    except ValueError:
        return None
    if expiry is None or c.right not in ("C", "P"):
        return None
    return (product_code(c.tradingClass), expiry, round(c.strike, 4), c.right, size)


def _row_key(product: str, row: CatalogRow) -> Key:
    right = "C" if row.right is Right.CALL else "P"
    return (product, row.expiration, round(float(row.strike), 4), right, int(row.contract_size))


def match(product: str, rows: list[CatalogRow], contracts: list[ContractInfo]) -> MatchResult:
    """Link ``rows`` (one underlying's live Eurex contracts) to ``contracts``."""
    ibkr: dict[Key, list[int]] = defaultdict(list)
    for c in contracts:
        key = _ibkr_key(c)
        if key is not None:
            ibkr[key].append(c.conId)
    stored: dict[Key, list[int]] = defaultdict(list)
    for row in rows:
        stored[_row_key(product, row)].append(row.id)

    result = MatchResult()
    for key, row_ids in stored.items():
        candidates = ibkr.get(key, [])
        if len(row_ids) == 1 and len(candidates) == 1:
            result.links[row_ids[0]] = candidates[0]
        elif not candidates:
            result.unmatched_rows += len(row_ids)
        else:
            result.ambiguous += len(row_ids)
    result.unmatched_ibkr = sum(
        len(ids) for key, ids in ibkr.items() if key[0] == product and key not in stored
    )
    return result
