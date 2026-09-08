"""Eurex contract identity.

Eurex has no OCC symbol, and nothing that plays the same role. What it has
instead is better in one way and worse in another.

Better: every contract carries an **ISIN**, which is globally unique, issued
by a national numbering agency, and stable. ``DE000F2P1J24`` identifies one
BMW option and nothing else, anywhere. As a database key it beats a
constructed string outright.

Worse: an ISIN is opaque. It encodes nothing about strike, expiry or right, so
unlike an OCC symbol it cannot validate the feed's own metadata against
itself. The terms arrive as separate fields and are simply believed. That
asymmetry is documented at length in ``domain/contracts.py``; it is the real
cost of leaving the US symbology behind.

The stored key is the ISIN with an ``EUX:`` prefix. The prefix exists purely so
the two namespaces provably cannot collide in the ``option_contracts.symbol``
unique index: no OCC symbol contains a colon, so no Eurex key can ever be
mistaken for one, and a glance at a row tells you which venue it came from.
"""

from __future__ import annotations

from datetime import date

from app.domain.contracts import ContractParseError, ContractRef, Right
from app.domain.venue import ExerciseStyle, Venue, spec_for

PREFIX = "EUX:"

_EUREX_SPEC = spec_for(Venue.EUREX)

# ISINs are 12 characters: 2-letter country code, 9 alphanumerics, 1 check
# digit. Validated loosely — the check digit is not verified, because a feed
# that starts emitting malformed ISINs is a problem to notice, not one to
# silently correct.
_ISIN_LEN = 12


class EurexParseError(ContractParseError):
    """A string is not a well-formed Eurex contract key."""


def key_for(isin: str) -> str:
    """The stored natural key for a Eurex contract, from its ISIN."""
    raw = isin.strip().upper()
    if len(raw) != _ISIN_LEN or not raw.isalnum():
        raise EurexParseError(f"Not a well-formed ISIN: {isin!r}")
    return f"{PREFIX}{raw}"


def isin_of(symbol: str) -> str:
    """The ISIN inside a stored Eurex key (the inverse of :func:`key_for`)."""
    if not symbol.startswith(PREFIX):
        raise EurexParseError(f"Not a Eurex contract key: {symbol!r}")
    return symbol[len(PREFIX):]


def is_eurex_key(symbol: str) -> bool:
    """Whether a stored symbol belongs to the Eurex namespace."""
    return symbol.startswith(PREFIX)


def contract_ref(
    *,
    isin: str,
    product: str,
    expiration: date,
    right: Right,
    strike: float,
    exercise_style: ExerciseStyle | None = None,
    contract_size: int | None = None,
    currency: str | None = None,
    id: int | None = None,
) -> ContractRef:
    """Build a :class:`ContractRef` from the fields the Eurex feed publishes.

    Unlike :func:`app.domain.occ.parse_occ` this is construction, not parsing:
    there is no redundancy in the identifier to check the terms against, so
    the feed's fields are taken as given. The only validation is that the ISIN
    is shaped like an ISIN.

    ``exercise_style`` is passed through rather than defaulted whenever the
    feed supplies it, because Eurex lists both styles on the same underlying
    (BMW has an American class and a European one at the same expiry) and
    guessing would be wrong roughly half the time on those names.
    """
    return ContractRef(
        symbol=key_for(isin),
        venue=Venue.EUREX,
        root=product.strip().upper(),
        expiration=expiration,
        right=right,
        strike=float(strike),
        exercise_style=exercise_style or _EUREX_SPEC.default_exercise_style,
        contract_size=contract_size or _EUREX_SPEC.default_contract_size,
        currency=currency or _EUREX_SPEC.currency,
        id=id,
    )


def right_from_callput(call_put: str) -> Right:
    """Map the feed's ``CallPut`` field ('C' / 'P') onto :class:`Right`."""
    token = (call_put or "").strip().upper()
    if token.startswith("C"):
        return Right.CALL
    if token.startswith("P"):
        return Right.PUT
    raise EurexParseError(f"Unrecognised CallPut value: {call_put!r}")


def exercise_from_feed(value: str | None) -> ExerciseStyle:
    """Map the feed's ``ExerciseStyle`` field onto :class:`ExerciseStyle`.

    Falls back to the venue default rather than raising: an unknown exercise
    style is a reason to keep collecting and flag it, not a reason to drop a
    contract from the catalogue.
    """
    token = (value or "").strip().upper()
    if token.startswith("EURO"):
        return ExerciseStyle.EUROPEAN
    if token.startswith("AMER"):
        return ExerciseStyle.AMERICAN
    return _EUREX_SPEC.default_exercise_style
