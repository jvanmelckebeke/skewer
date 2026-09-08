"""The venue-agnostic identity of one option contract.

``ContractRef`` used to live in ``domain/occ.py`` and *was* a parsed OCC
symbol. That worked while every contract skewer knew about was US-listed, and
stopped working the moment Eurex arrived, because a Eurex contract has no OCC
symbol to parse.

The split is now:

* this module owns the *identity* (root, expiration, right, strike, and the
  venue-dependent facts that follow from those),
* ``domain/occ.py`` owns the OCC *symbology* (the 21-character string),
* ``domain/eurex.py`` owns the Eurex symbology (an ISIN-based key).

The identity is still frozen and self-contained, for the original reason: it
is captured once and carried through batch loops that commit and roll back,
and a rollback expires every live ORM instance in the session. A frozen ref
cannot expire; an ORM row would raise ``MissingGreenlet``.

One difference between the venues is worth stating plainly rather than
papering over. An OCC symbol *encodes* strike, expiry and right, so parsing it
is also validation: a provider that mislabels a row contradicts its own
symbol and gets rejected at ingest. A Eurex key carries no such redundancy,
because the feed publishes the terms as fields. There is nothing to cross-check
them against, so for Eurex the feed is simply trusted. That is a genuine
reduction in safety, not an equivalent design, and it is the price of the
venue not encoding its terms in its identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from app.domain.venue import ExerciseStyle, Venue, spec_for


class Right(str, Enum):
    """Which side of the chain a contract sits on."""

    CALL = "call"
    PUT = "put"

    @property
    def sign(self) -> int:
        """+1 for a call, -1 for a put — the ``phi`` of the Black-Scholes
        formulas, so pricing code branches on arithmetic rather than on an
        ``if``."""
        return 1 if self is Right.CALL else -1


class ContractParseError(ValueError):
    """A string is not a well-formed contract identifier for its venue."""


# Strike is stored in the OCC symbol as thousandths of a currency unit. The
# scale is reused as the strike-comparison tolerance for every venue, since it
# is finer than any listed tick.
STRIKE_SCALE = 1000


@dataclass(frozen=True, slots=True)
class ContractRef:
    """One option contract's identity, independent of who reported it.

    ``symbol`` is the natural key and is unique across venues. For OCC it is
    the bare 21-character symbol (unchanged, so existing rows keep working);
    for Eurex it is an ``EUX:``-prefixed ISIN. The prefix is what guarantees
    the two namespaces cannot collide, since no OCC root contains a colon.
    """

    symbol: str
    venue: Venue
    root: str
    expiration: date
    right: Right
    strike: float

    # Venue-dependent, but overridable per contract because Eurex publishes
    # both per row and lists both exercise styles on the same underlying.
    exercise_style: ExerciseStyle = ExerciseStyle.AMERICAN
    contract_size: int = 100
    currency: str = "USD"

    id: int | None = None

    @classmethod
    def parse(cls, occ_symbol: str, id: int | None = None) -> ContractRef:
        """Parse an OCC symbol.

        Retained as a classmethod because it predates multi-venue support and
        is called from a dozen places and several tests. The OCC grammar
        itself lives in ``domain/occ.py``, which imports this module — hence
        the function-local import, which keeps the module-level dependency
        one-directional.
        """
        from app.domain.occ import parse_occ

        return parse_occ(occ_symbol, id=id)

    @classmethod
    def build(
        cls, root: str, expiration: date, right: Right, strike: float
    ) -> ContractRef:
        """Construct an OCC contract from its terms (the inverse of parse)."""
        from app.domain.occ import build_occ

        return build_occ(root, expiration, right, strike)

    @property
    def occ_symbol(self) -> str:
        """Backwards-compatible alias.

        A great deal of code, and the ``option_contracts.symbol`` column's
        former name, called this ``occ_symbol``. Keeping the alias means the
        Eurex work did not have to rename every call site in one commit. It is
        only meaningful when ``venue is Venue.OCC``.
        """
        return self.symbol

    def expires_at(self) -> datetime:
        """The contract's expiry instant, timezone-aware.

        Delegates to the venue: 16:00 America/New_York for OCC, 17:30
        Europe/Berlin for Eurex. Time to expiry drives every greek, and at
        0DTE the gap between "expires at midnight" and "expires at the close"
        is the option's whole remaining life.
        """
        return spec_for(self.venue).expires_at(self.expiration)

    def year_fraction(self, now: datetime) -> float:
        """Time to expiry in years (ACT/365), floored at zero.

        Returns exactly ``0.0`` for an expired contract; pricing code treats
        that as intrinsic value rather than dividing by ``sqrt(0)``.
        """
        seconds = (self.expires_at() - now).total_seconds()
        return max(seconds, 0.0) / (365.0 * 86400.0)

    def dte(self, on: date) -> int:
        """Whole calendar days to expiry, the unit the cadence buckets use."""
        return (self.expiration - on).days

    def matches(self, strike: float | None, expiration: date | None) -> bool:
        """Whether provider-reported metadata agrees with this identity.

        Only meaningful for a venue whose identifier encodes its terms, which
        today means OCC. A tolerance of half a strike tick absorbs the float
        representation of the provider's decimal strike without admitting a
        genuinely different one.
        """
        if expiration is not None and expiration != self.expiration:
            return False
        if strike is not None and abs(strike - self.strike) > 0.5 / STRIKE_SCALE:
            return False
        return True

    def __str__(self) -> str:
        return self.symbol
