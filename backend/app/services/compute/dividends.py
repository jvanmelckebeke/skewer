"""The dividend yield, measured off the chain instead of assumed.

``q`` is not a property of the company, it is what the option market currently
believes the company will pay before expiry. That belief is already in the
chain: a call and a put at the same strike and expiry pin the forward through
put-call parity,

    C - P = e^(-rT) * (F - K)      =>      F = K + e^(rT) * (C - P)

and the forward gives the yield back,

    q = r - ln(F / S) / T

which is a measurement rather than a number typed in from a fact sheet. It is
also per-expiry, and it has to be: dividends are seasonal, so one constant is
wrong at both ends of the term structure. An index makes this obvious. The
EURO STOXX 50 is a price index, so its level drops on every constituent's
ex-date, and the aggregate of fifty payout schedules is a genuinely smooth
leak of about 3% a year. A single stock paying once in May is not smooth at
all, and the continuous form is a cruder fit there no matter what number goes
in it.

Three things this module refuses to do, all for the same reason the IV solver
refuses to clamp to its bracket:

**It does not pretend parity is exact on American options.** ``C - P = ...``
is a European identity. American exercise turns it into an inequality band,
because the right to exercise early is worth something that parity has no term
for. Every OCC contract skewer stores is American. The mitigation is to
measure near the money, where the early-exercise premium is smallest, and the
honest statement is that this is a good approximation there and a bad one in
the wings. ``ExerciseStyle`` is stored per contract, so the day a binomial
path lands this can dispatch on it.

**It does not measure at short tenor.** ``ln(F/S)/T`` divides by the tenor, so
at 1DTE a parity error of one tick becomes a yield of tens of percent. The
floor is a refusal, not a correction.

**It does not clamp an implausible answer into the plausible band.** A yield
outside the band is evidence the pairs were bad, not evidence of a 40% payout,
and returning the edge would manufacture exactly the artefact ``MIN_VEGA``
exists to prevent. Note that a *negative* yield is not implausible: on a
hard-to-borrow name the borrow cost surfaces here, and carrying it into the
pricing is correct rather than a bug to be filtered out.
"""

from __future__ import annotations

import numpy as np

# Below this the division by T dominates. Two weeks is where a one-cent parity
# error stops being able to move the answer by more than a percentage point.
MIN_TENOR_YEARS = 14 / 365.0

# Parity holds at every strike in theory. In practice the estimate is only as
# good as the worse of the two mids, and a deep in-the-money option is nearly
# all intrinsic with a wide relative spread — plus, on American contracts, the
# largest early-exercise premium. Both arguments point at the same window.
#
# The window is a preference rather than a fixed width, because strike spacing
# varies by two orders of magnitude across the tracked names: NVDA at 224 lists
# dollar strikes, AVEX at 17 lists them 2.50 apart, and a fixed ±10% band would
# give the first forty pairs and the second one. So pairs are ranked by
# distance from the money and the nearest few are taken, with the band as an
# outer limit rather than the selector. A chain sparse enough that the limit
# binds gets a worse measurement, and that is the honest outcome — the
# alternative is refusing to measure the thin names at all.
MONEYNESS_BAND = 0.20
MAX_PAIRS = 8

# One pair is an anecdote: a single stale mid on either leg moves it without
# leaving any trace. Three lets the median discard one.
MIN_PAIRS = 3

# Wide on purpose. The band exists to reject a broken measurement, not to
# express a view about what a sensible payout ratio is, and both edges are
# real: a hard-to-borrow name implies negative, and a special dividend or a
# high-payout single stock reaches well past a typical index.
MIN_YIELD = -0.15
MAX_YIELD = 0.35


def implied_yield_by_expiry(
    spot: float,
    strike: np.ndarray,
    tenor: np.ndarray,
    is_call: np.ndarray,
    mark: np.ndarray,
    rate: float,
) -> dict[float, float]:
    """Implied continuous dividend yield, keyed by tenor.

    The key is the tenor in years rather than an expiration date, because that
    is what every caller already has aligned with its arrays and it survives
    the snapshot/chain split without either side needing a date column.

    Expiries where parity cannot answer are absent from the result rather than
    present with a fallback. Deciding what to do about a missing measurement
    belongs to the caller, which is the only layer that knows what the stored
    yield is and whether a human set it.
    """
    if not (spot > 0):
        return {}

    strike = np.asarray(strike, dtype=float)
    tenor = np.asarray(tenor, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)
    mark = np.asarray(mark, dtype=float)

    usable = np.isfinite(mark) & (mark > 0) & np.isfinite(strike) & (strike > 0)
    near = np.abs(np.log(strike / spot, where=strike > 0, out=np.full_like(strike, np.inf)))
    usable &= near <= MONEYNESS_BAND
    usable &= tenor >= MIN_TENOR_YEARS

    out: dict[float, float] = {}
    for t in np.unique(tenor[usable]):
        here = usable & (tenor == t)
        calls = {k: m for k, m in zip(strike[here & is_call], mark[here & is_call])}
        puts = {k: m for k, m in zip(strike[here & ~is_call], mark[here & ~is_call])}

        # Nearest the money first, then take a handful. See MAX_PAIRS.
        shared = sorted(set(calls) & set(puts), key=lambda k: abs(np.log(k / spot)))
        shared = shared[:MAX_PAIRS]
        if len(shared) < MIN_PAIRS:
            continue

        # One forward per strike, then the median. The mean would let a single
        # crossed or stale mid drag the estimate; parity's whole appeal here is
        # that every pair is an independent estimate of the same quantity.
        forwards = np.array(
            [k + np.exp(rate * t) * (calls[k] - puts[k]) for k in shared], dtype=float
        )
        forward = float(np.median(forwards[np.isfinite(forwards)]))
        if not (forward > 0):
            continue

        q = rate - np.log(forward / spot) / t
        if not np.isfinite(q) or q < MIN_YIELD or q > MAX_YIELD:
            continue
        out[float(t)] = float(q)

    return out


def yield_per_contract(
    by_expiry: dict[float, float], tenor: np.ndarray, fallback: float
) -> np.ndarray:
    """Broadcast per-expiry yields back onto a contract-aligned array.

    Contracts in an expiry parity could not answer for get ``fallback``, which
    keeps them priced against the caller's best available number instead of
    dropping them from the solve entirely.
    """
    tenor = np.asarray(tenor, dtype=float)
    out = np.full(tenor.shape, float(fallback), dtype=float)
    for t, q in by_expiry.items():
        out[tenor == t] = q
    return out


def representative_yield(by_expiry: dict[float, float]) -> float | None:
    """One number for the whole surface, or None if there is nothing to report.

    The median across expiries rather than the front one. The front expiry is
    both the noisiest (shortest tenor, so the largest divisor effect) and the
    least representative, since it may sit entirely before or entirely after
    the next ex-date while the rest of the surface straddles several.
    """
    values = [q for q in by_expiry.values() if np.isfinite(q)]
    if not values:
        return None
    return float(np.median(values))
