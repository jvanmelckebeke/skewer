"""OCC option-symbol identity.

An option contract's identity is fully encoded in its OCC symbol — the
21-character string every US venue and Yahoo agree on:

    NVDA260821C00002500
    ^^^^ ^^^^^^ ^ ^^^^^^^^
    root  YYMMDD | strike x 1000, 8 digits, zero-padded
                 C=call / P=put

This module is the *only* place that string is interpreted or constructed.
Everything downstream asks a :class:`ContractRef` for ``.strike`` / ``.right``
/ ``.expiration`` rather than re-slicing characters — the same rule fibenchi
applies to ticker shape in ``domain/instrument.classify``.

Parsing is not merely a convenience: it is a *validator*. The provider's
``strike`` and ``expiration`` fields are re-derived from the symbol and
cross-checked at ingest (:func:`ContractRef.parse`), so a provider that
mislabels a row can't silently write a contract whose metadata contradicts
its own identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

# US equity options expire at 16:00 America/New_York on their expiration date.
# (Most listed equity options have been PM-settled since 2015; a handful of
# index options are AM-settled, which we do not track yet — see EXPIRY_TZ note
# in docstring of `expires_at`.)
EXPIRY_TZ = ZoneInfo("America/New_York")
EXPIRY_TIME = time(16, 0)

# Root + YYMMDD + C|P + 8-digit strike. The root is 1-6 alphanumerics (plus the
# rare '.' / '-' used by adjusted classes, e.g. 'BRK.B'-style roots).
_OCC_RE = re.compile(r"^(?P<root>[A-Z0-9.\-]{1,6})(?P<ymd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")

# The strike field is an integer count of thousandths of a currency unit.
STRIKE_SCALE = 1000


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


class OccParseError(ValueError):
    """Raised when a string is not a well-formed OCC option symbol."""


@dataclass(frozen=True, slots=True)
class ContractRef:
    """A parsed OCC symbol: the domain identity of one option contract.

    Frozen and self-contained, like fibenchi's ``AssetRef`` — it is captured
    once and carried through batch loops that commit and roll back, so it can
    never be an expired ORM instance.
    """

    occ_symbol: str
    root: str
    expiration: date
    right: Right
    strike: float
    id: int | None = None

    @classmethod
    def parse(cls, occ_symbol: str, id: int | None = None) -> ContractRef:
        """Parse an OCC symbol, or raise :class:`OccParseError`."""
        raw = occ_symbol.strip().upper()
        m = _OCC_RE.match(raw)
        if not m:
            raise OccParseError(f"Not a well-formed OCC option symbol: {occ_symbol!r}")
        ymd = m.group("ymd")
        try:
            # OCC uses a 2-digit year. Listed options never span the 100-year
            # ambiguity (the longest LEAPS is ~3y), so 20xx is unconditional.
            expiration = date(2000 + int(ymd[0:2]), int(ymd[2:4]), int(ymd[4:6]))
        except ValueError as exc:
            raise OccParseError(f"{occ_symbol!r} encodes an impossible date {ymd!r}") from exc
        return cls(
            occ_symbol=raw,
            root=m.group("root"),
            expiration=expiration,
            right=Right.CALL if m.group("right") == "C" else Right.PUT,
            strike=int(m.group("strike")) / STRIKE_SCALE,
            id=id,
        )

    @classmethod
    def build(cls, root: str, expiration: date, right: Right, strike: float) -> ContractRef:
        """Construct the OCC symbol for a contract (the inverse of parse)."""
        ticks = round(strike * STRIKE_SCALE)
        if ticks <= 0 or ticks > 99_999_999:
            raise OccParseError(f"Strike {strike!r} is out of OCC range")
        occ = f"{root.upper()}{expiration:%y%m%d}{'C' if right is Right.CALL else 'P'}{ticks:08d}"
        return cls(occ, root.upper(), expiration, right, ticks / STRIKE_SCALE)

    def expires_at(self) -> datetime:
        """The contract's expiry instant, timezone-aware.

        Time to expiry drives every greek, and at 0DTE the difference between
        "expires at midnight" and "expires at the 16:00 ET close" is the whole
        remaining life of the option — so this is a real correctness concern,
        not a rounding detail.
        """
        return datetime.combine(self.expiration, EXPIRY_TIME, tzinfo=EXPIRY_TZ)

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
        """Whether provider-reported metadata agrees with the parsed symbol.

        A tolerance of half a strike tick absorbs float representation of the
        provider's decimal strike without admitting a genuinely different one.
        """
        if expiration is not None and expiration != self.expiration:
            return False
        if strike is not None and abs(strike - self.strike) > 0.5 / STRIKE_SCALE:
            return False
        return True

    def __str__(self) -> str:
        return self.occ_symbol
