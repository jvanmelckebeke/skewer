"""OCC option-symbol identity — the US symbology.

An OCC contract's identity is fully encoded in its symbol, the 21-character
string every US venue and Yahoo agree on:

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
cross-checked at ingest (:meth:`ContractRef.matches`), so a provider that
mislabels a row can't silently write a contract whose metadata contradicts its
own identity. No other venue skewer supports gives that guarantee for free —
see the note in ``domain/contracts.py``.

``ContractRef`` and ``Right`` now live in ``domain/contracts.py`` because they
describe any venue's contract, not just an OCC one. They are re-exported here
so the many modules that import them from this path keep working.
"""

from __future__ import annotations

import re
from datetime import date, time
from zoneinfo import ZoneInfo

from app.domain.contracts import (  # noqa: F401  (re-exported)
    STRIKE_SCALE,
    ContractParseError,
    ContractRef,
    Right,
)
from app.domain.venue import Venue, spec_for

# Kept as module-level names because a lot of code and several tests refer to
# them. They are now derived from the venue spec rather than declared here, so
# there is exactly one definition of when a US option expires.
_OCC_SPEC = spec_for(Venue.OCC)
EXPIRY_TZ: ZoneInfo = _OCC_SPEC.expiry_tz
EXPIRY_TIME: time = _OCC_SPEC.expiry_time

# Root + YYMMDD + C|P + 8-digit strike. The root is 1-6 alphanumerics (plus the
# rare '.' / '-' used by adjusted classes, e.g. 'BRK.B'-style roots).
_OCC_RE = re.compile(r"^(?P<root>[A-Z0-9.\-]{1,6})(?P<ymd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")


class OccParseError(ContractParseError):
    """Raised when a string is not a well-formed OCC option symbol.

    A subclass of :class:`ContractParseError` so that ingest code can catch
    either the venue-specific or the general failure, depending on whether it
    handles one symbology or several.
    """


def parse_occ(occ_symbol: str, id: int | None = None) -> ContractRef:
    """Parse an OCC symbol into a :class:`ContractRef`, or raise.

    The returned ref is stamped ``Venue.OCC``, which is what makes its
    ``expires_at()`` resolve to 16:00 America/New_York.
    """
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
    return ContractRef(
        symbol=raw,
        venue=Venue.OCC,
        root=m.group("root"),
        expiration=expiration,
        right=Right.CALL if m.group("right") == "C" else Right.PUT,
        strike=int(m.group("strike")) / STRIKE_SCALE,
        exercise_style=_OCC_SPEC.default_exercise_style,
        contract_size=_OCC_SPEC.default_contract_size,
        currency=_OCC_SPEC.currency,
        id=id,
    )


def build_occ(root: str, expiration: date, right: Right, strike: float) -> ContractRef:
    """Construct the OCC symbol for a contract (the inverse of :func:`parse_occ`)."""
    ticks = round(strike * STRIKE_SCALE)
    if ticks <= 0 or ticks > 99_999_999:
        raise OccParseError(f"Strike {strike!r} is out of OCC range")
    occ = f"{root.upper()}{expiration:%y%m%d}{'C' if right is Right.CALL else 'P'}{ticks:08d}"
    return ContractRef(
        symbol=occ,
        venue=Venue.OCC,
        root=root.upper(),
        expiration=expiration,
        right=right,
        strike=ticks / STRIKE_SCALE,
        exercise_style=_OCC_SPEC.default_exercise_style,
        contract_size=_OCC_SPEC.default_contract_size,
        currency=_OCC_SPEC.currency,
    )
