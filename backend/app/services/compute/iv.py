"""Implied volatility, re-solved from our own marks.

Yahoo ships an ``impliedVolatility`` field and it cannot be trusted. On the
real NVDA chain, deep in-the-money strikes come back as ``1.0000000000000003e-05``
— that is not a 0.001% volatility, it is a solver that failed and reported its
lower bound. Storing that as data would put a hard floor artefact into every
smile, skew and term-structure plot built on top of it.

So skewer solves IV itself, from the mid of a genuine two-sided quote, and is
willing to return **nothing**. That willingness is the point: an option with no
bid, a crossed book, or a price outside the no-arbitrage bounds has no implied
volatility, and the honest representation of that is NULL plus a reason code —
not a number that plots.

Method: bisection, vectorised over the chain. Newton-Raphson converges faster
but its step is ``price_error / vega``, and vega goes to ~0 for deep ITM/OTM
and near-expiry contracts — exactly the strikes where Yahoo's own solver is
visibly failing. Dividing by a near-zero vega there is how you get 1e-05.
Bisection has no such failure mode: the price is monotonic in sigma, so a
bracketed root is always found, and ~60 iterations on a fixed bracket costs
one vector op each. For a 3,000-contract chain that is single-digit
milliseconds, which is cheap enough that robustness wins outright.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.services.compute.bs import greeks, no_arbitrage_bounds, price

# The bracket. 1% annualised is below any real listed equity option; 500% is
# above anything short of a biotech binary event. A root outside this range is
# treated as no root at all rather than clamped to the edge — clamping is
# exactly the failure that produces Yahoo's 1e-05.
SIGMA_LO = 0.01
SIGMA_HI = 5.0

# Bisection halves the bracket each step: 60 iterations takes a range of ~5.0
# down to ~4e-18, far below the precision the input prices justify. Fixed
# rather than tolerance-based so the whole vector finishes together — an
# early-exit loop over a chain is slower, not faster.
ITERATIONS = 60

# A price this close to the no-arbitrage boundary carries no volatility
# information: every sigma in the bracket reproduces it to within a cent, so
# whatever the solver returns is an artefact of the bracket, not a measurement.
BOUND_EPS = 1e-6

# The identifiability gate, and the most important guard in this module.
#
# Bisection always converges — that is its virtue and its trap. For a deep
# in-the-money contract the price is ~100% intrinsic and vega is ~0, so *every*
# sigma in [SIGMA_LO, SIGMA_HI] reproduces the quote to within a fraction of a
# tick. The solver duly returns a number, and that number is whichever bracket
# edge it drifted to. That is precisely how Yahoo ends up publishing 1e-05 on
# deep-ITM NVDA strikes, and an unguarded bisection reproduces the artefact
# faithfully.
#
# So the criterion is not "did it converge" but "is the price actually
# sensitive to sigma at the resolution the quote is published in". Listed
# options quote in $0.01 ticks. If a full volatility point (0.01 of sigma)
# moves the model price by less than half a tick, the market simply has not
# expressed an opinion about volatility at a resolution we can read, and the
# honest answer is NULL.
#
#   vega is per 1.00 of sigma, so per vol point it is vega/100.
#   require vega/100 >= 0.005  =>  vega >= 0.5
MIN_VEGA = 0.5


class IVStatus:
    """Why an IV is present or absent. Stored on the quote row, so a missing
    IV is diagnosable instead of mysterious."""

    OK = "ok"
    NO_QUOTE = "no_quote"          # no two-sided bid/ask to take a mid from
    EXPIRED = "expired"            # no time value left to imply anything from
    BELOW_INTRINSIC = "below_intr" # quote violates the lower no-arb bound
    ABOVE_BOUND = "above_bound"    # quote violates the upper no-arb bound
    NO_ROOT = "no_root"            # bracketed solve did not converge in range
    NO_VEGA = "no_vega"            # price is insensitive to sigma; IV unidentifiable
    FX_MISMATCH = "fx_mismatch"    # spot and strikes are in different currencies


@dataclass(frozen=True, slots=True)
class IVResult:
    """Solved vols aligned with the input arrays, plus a per-contract status."""

    iv: np.ndarray       # float array, NaN where unsolved
    status: np.ndarray   # object array of IVStatus values

    @property
    def solved(self) -> np.ndarray:
        return ~np.isnan(self.iv)


def implied_vol(
    market_price: np.ndarray,
    s: np.ndarray,
    k: np.ndarray,
    t: np.ndarray,
    r: float,
    q: np.ndarray,
    phi: np.ndarray,
) -> IVResult:
    """Solve for sigma such that BSM price == ``market_price``.

    Returns NaN (with a status) wherever no volatility can be implied, rather
    than a fabricated number. Inputs broadcast; all arrays come back aligned
    with the broadcast shape.
    """
    mp, s, k, t, q, phi = np.broadcast_arrays(
        *(np.asarray(x, dtype=float) for x in (market_price, s, k, t, q, phi))
    )
    shape = mp.shape
    iv = np.full(shape, np.nan, dtype=float)
    status = np.full(shape, IVStatus.OK, dtype=object)

    usable = np.isfinite(mp) & (mp > 0) & (s > 0) & (k > 0)
    status[~usable] = IVStatus.NO_QUOTE

    expired = usable & ~(t > 0)
    status[expired] = IVStatus.EXPIRED
    usable = usable & ~expired

    # Reject prices no volatility can produce. Doing this *before* the solve is
    # what separates "this quote is unusable" from "the solver picked an edge".
    lo_b, hi_b = no_arbitrage_bounds(s, k, np.where(usable, t, 1.0), r, q, phi)
    below = usable & (mp < lo_b - BOUND_EPS)
    above = usable & (mp > hi_b - BOUND_EPS)
    status[below] = IVStatus.BELOW_INTRINSIC
    status[above] = IVStatus.ABOVE_BOUND
    usable = usable & ~below & ~above

    if not usable.any():
        return IVResult(iv=iv, status=status)

    ts = np.where(usable, t, 1.0)
    lo = np.full(shape, SIGMA_LO)
    hi = np.full(shape, SIGMA_HI)

    # The root must be bracketed for bisection to mean anything. A price above
    # the value at SIGMA_HI (or below that at SIGMA_LO) is out of range — and
    # "out of range" is reported, never clamped.
    p_lo = price(s, k, ts, lo, r, q, phi)
    p_hi = price(s, k, ts, hi, r, q, phi)
    bracketed = usable & (mp >= p_lo) & (mp <= p_hi)
    status[usable & ~bracketed] = IVStatus.NO_ROOT

    if not bracketed.any():
        return IVResult(iv=iv, status=status)

    for _ in range(ITERATIONS):
        mid = 0.5 * (lo + hi)
        p_mid = price(s, k, ts, mid, r, q, phi)
        # Price is monotonically increasing in sigma, so the side to keep is
        # decided by a single comparison — no derivative, no division.
        too_low = p_mid < mp
        lo = np.where(too_low, mid, lo)
        hi = np.where(too_low, hi, mid)

    solved = 0.5 * (lo + hi)

    # Identifiability gate — see MIN_VEGA. Evaluated at the solved sigma
    # because vega depends on it: a contract can be sensitive at one volatility
    # and numb at another, so the question is only answerable after the solve.
    vega = greeks(s, k, ts, np.where(bracketed, solved, 0.3), r, q, phi).vega
    identifiable = bracketed & (vega >= MIN_VEGA)
    status[bracketed & ~identifiable] = IVStatus.NO_VEGA

    iv = np.where(identifiable, solved, iv)
    return IVResult(iv=iv, status=status)


def mid_price(bid: np.ndarray, ask: np.ndarray) -> np.ndarray:
    """Mid of a genuine two-sided quote, NaN otherwise.

    The zero check is not defensive padding — it is the single most important
    data-quality rule in skewer. Yahoo returns ``bid=0.0, ask=0.0`` for the
    large majority of contracts whenever the market is not in regular session
    (2,948 of NVDA's 3,286 while in PRE). Treating those as a mid of zero would
    write a zero mark and a garbage IV for most of the chain, every night.
    """
    bid = np.asarray(bid, dtype=float)
    ask = np.asarray(ask, dtype=float)
    valid = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > 0) & (ask >= bid)
    return np.where(valid, 0.5 * (bid + ask), np.nan)
