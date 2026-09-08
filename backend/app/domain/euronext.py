"""Euronext contract identity.

Euronext's derivatives web service identifies a contract by the string it also
takes as a URL parameter:

    PHIA-C-202612-34.00
    ^^^^ ^ ^^^^^^ ^^^^^
    code | YYYYMM strike
         C=call / P=put

That is closer to OCC than to Eurex: the terms are *in* the identifier, so
parsing it validates the feed's own metadata the way an OCC symbol does. What
it lacks is OCC's fixed width, and crucially the maturity is only a **month**.
The exact expiry date arrives separately as ``instr.exchEndDt``, so a ref built
from the identifier alone knows the month but not the day.

That matters more than it sounds. Euronext equity options expire on the third
Friday, and guessing that instead of reading ``exchEndDt`` would be wrong on
every holiday-shifted expiry. So :func:`contract_ref` requires the real
expiration and the identifier is used only for the request and the key.

The stored key is ``XNT:<EXCODE>:<identifier>``. The exchange code is part of
the key because the same contract code can exist on more than one Euronext
market, and they are genuinely different contracts.
"""

from __future__ import annotations

import re
from datetime import date

from app.domain.contracts import ContractParseError, ContractRef, Right
from app.domain.venue import ExerciseStyle, Venue, spec_for

PREFIX = "XNT:"

_SPEC = spec_for(Venue.EURONEXT)

# The five markets the derivatives spec lists, mapped to something readable.
EXCHANGES: dict[str, str] = {
    "DAMS": "Euronext Amsterdam",
    "DBRU": "Euronext Brussels",
    "DLIS": "Euronext Lisbon",
    "DOSL": "Oslo Bors",
    "DPAR": "Euronext Paris",
}

DEFAULT_EXCHANGE = "DAMS"

# code - C|P - YYYYMM - strike. The contract code is alphanumeric (plus the
# occasional digit-suffixed adjusted class); the strike is a plain decimal.
_ID_RE = re.compile(
    r"^(?P<code>[A-Z0-9]{1,12})-(?P<right>[CP])-(?P<maturity>\d{6})-(?P<strike>\d+(?:\.\d+)?)$"
)


class EuronextParseError(ContractParseError):
    """A string is not a well-formed Euronext contract identifier or key."""


def instrument_code(
    code: str, right: Right, maturity: date | tuple[int, int], strike: float,
    strike_decimals: int = 2,
) -> str:
    """Build the ``code-TYPE-YYYYMM-STRIKE`` identifier the API expects.

    ``strike_decimals`` exists because the API matches this string literally
    and the spec gives no canonical formatting for the strike. Two decimals is
    the common case for equity options; index options on a four-figure level
    are usually integers. It is configurable rather than guessed, because a
    mismatch here is an empty response rather than an error.
    """
    if isinstance(maturity, date):
        year, month = maturity.year, maturity.month
    else:
        year, month = maturity
    letter = "C" if right is Right.CALL else "P"
    return f"{code.strip().upper()}-{letter}-{year:04d}{month:02d}-{strike:.{strike_decimals}f}"


def key_for(identifier: str, exchange: str = DEFAULT_EXCHANGE) -> str:
    """The stored natural key for a Euronext contract."""
    exch = (exchange or DEFAULT_EXCHANGE).strip().upper()
    if exch not in EXCHANGES:
        raise EuronextParseError(f"Unknown Euronext exchange code: {exchange!r}")
    return f"{PREFIX}{exch}:{identifier.strip().upper()}"


def is_euronext_key(symbol: str) -> bool:
    """Whether a stored symbol belongs to the Euronext namespace."""
    return symbol.startswith(PREFIX)


def split_key(symbol: str) -> tuple[str, str]:
    """``(exchange, identifier)`` from a stored key."""
    if not is_euronext_key(symbol):
        raise EuronextParseError(f"Not a Euronext contract key: {symbol!r}")
    body = symbol[len(PREFIX):]
    exch, _, identifier = body.partition(":")
    if not identifier:
        raise EuronextParseError(f"Malformed Euronext key: {symbol!r}")
    return exch, identifier


def parse_identifier(identifier: str) -> dict:
    """Pull the terms back out of an identifier.

    Returns the *month*, not a date: the identifier does not carry the expiry
    day, and inventing one would be exactly the kind of quiet fiction the
    no-interpolation rule exists to prevent.
    """
    raw = identifier.strip().upper()
    m = _ID_RE.match(raw)
    if not m:
        raise EuronextParseError(f"Not a well-formed Euronext identifier: {identifier!r}")
    maturity = m.group("maturity")
    year, month = int(maturity[:4]), int(maturity[4:])
    if not 1 <= month <= 12:
        raise EuronextParseError(f"{identifier!r} encodes an impossible month {maturity!r}")
    return {
        "code": m.group("code"),
        "right": Right.CALL if m.group("right") == "C" else Right.PUT,
        "year": year,
        "month": month,
        "strike": float(m.group("strike")),
    }


def contract_ref(
    *,
    identifier: str,
    expiration: date,
    exchange: str = DEFAULT_EXCHANGE,
    exercise_style: ExerciseStyle | None = None,
    contract_size: int | None = None,
    currency: str | None = None,
    strike: float | None = None,
    id: int | None = None,
) -> ContractRef:
    """Build a ref from an identifier plus the real expiry date.

    ``expiration`` is required and is not derivable from ``identifier``, which
    carries only the maturity month. It comes from ``instr.exchEndDt``.

    ``strike`` overrides the one in the identifier, for the case where the
    feed's ``instr.strikePx`` disagrees after denominator scaling. When both
    are present and they disagree, that is a fault worth raising rather than
    silently preferring one — see :func:`check_strike`.
    """
    parsed = parse_identifier(identifier)
    if expiration.year != parsed["year"] or expiration.month != parsed["month"]:
        raise EuronextParseError(
            f"Expiration {expiration} contradicts identifier maturity "
            f"{parsed['year']:04d}{parsed['month']:02d} in {identifier!r}"
        )
    return ContractRef(
        symbol=key_for(identifier, exchange),
        venue=Venue.EURONEXT,
        root=parsed["code"],
        expiration=expiration,
        right=parsed["right"],
        strike=float(parsed["strike"] if strike is None else strike),
        exercise_style=exercise_style or _SPEC.default_exercise_style,
        contract_size=contract_size or _SPEC.default_contract_size,
        currency=currency or _SPEC.currency,
        id=id,
    )


def check_strike(identifier: str, feed_strike: float | None, tolerance: float = 0.005) -> None:
    """Cross-check the feed's strike against the one in the identifier.

    This is the Euronext equivalent of OCC parse-time validation, and the
    reason the venue is safer to ingest than Eurex: the identifier encodes the
    strike, so a feed row that disagrees with its own key is detectable rather
    than merely believed.

    The tolerance is half a cent, which absorbs the float representation of a
    decimal strike without admitting a genuinely different one.
    """
    if feed_strike is None:
        return
    expected = parse_identifier(identifier)["strike"]
    if abs(float(feed_strike) - expected) > tolerance:
        raise EuronextParseError(
            f"Feed strike {feed_strike!r} contradicts identifier {identifier!r} "
            f"(expected {expected})"
        )


def right_from_wartype(war_type: str | None) -> Right:
    """Map the feed's ``instr.warType`` ('C' / 'P') onto :class:`Right`."""
    token = (war_type or "").strip().upper()
    if token.startswith("C"):
        return Right.CALL
    if token.startswith("P"):
        return Right.PUT
    raise EuronextParseError(f"Unrecognised warType value: {war_type!r}")


def exercise_from_cfi(cfi: str | None) -> ExerciseStyle | None:
    """Read the exercise style out of an ISO 10962 CFI code.

    For an option the CFI is ``O`` + call/put + **exercise style** + …, so
    ``OCASPS`` is a call, American, and ``OPEXXX`` is a put, European. The
    feed publishes it as ``instr.cfiCode`` and it is the only place the style
    appears at all — unlike Eurex there is no dedicated field.

    Returns ``None`` rather than guessing when the code is too short or the
    character is unrecognised, so the caller falls back to the venue default
    knowingly.
    """
    token = (cfi or "").strip().upper()
    if len(token) < 3 or not token.startswith("O"):
        return None
    style = token[2]
    if style == "A":
        return ExerciseStyle.AMERICAN
    if style == "E":
        return ExerciseStyle.EUROPEAN
    # 'B' is Bermudan in ISO 10962. Skewer has no model for it and pretending
    # it is American would be a silent lie, so it reads as unknown.
    return None
