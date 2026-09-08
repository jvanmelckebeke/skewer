"""Black-Scholes-Merton pricing and greeks, vectorised over a whole chain.

Yahoo publishes **no greeks at all** — the option payload carries exactly
``bid, ask, lastPrice, volume, openInterest, impliedVolatility, inTheMoney``.
So every risk number skewer shows has to be computed here. That makes this
module the analogue of fibenchi's ``compute/indicators.py``: the pure,
I/O-free core that turns stored numbers into the things you actually look at.

Everything is written against numpy arrays rather than scalars. A single
underlying's chain is ~3,000 contracts and a surface query spans many
snapshots, so the scalar-loop version of this is the difference between a
millisecond and a second per request.

Conventions, stated once because mixing them is the classic source of greeks
that look plausible and are wrong:

* ``t`` is in **years**, ACT/365, measured to the 16:00 ET expiry instant.
* ``sigma``, ``r`` and ``q`` are **annualised decimals** (0.35 = 35%).
* ``vega`` is returned per **1.00** change in vol; divide by 100 for the
  "per vol point" number most UIs display (:func:`per_point`).
* ``theta`` is returned per **year**; divide by 365 for per-calendar-day
  (:func:`per_day`).
* Delta is the raw partial derivative — per 1 share, not per 100-share
  contract. Multiply by ``contract_size`` at the presentation edge only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

# Below these, the model has no meaningful answer: an option with no time left
# or no assumed volatility is worth exactly its intrinsic value, and every
# greek except delta collapses to zero. Guarding here rather than letting
# `sqrt(0)` produce inf/nan keeps the arrays finite all the way through.
MIN_T = 1e-9
MIN_SIGMA = 1e-9

# `phi` is +1 for calls and -1 for puts, which collapses the call and put
# formulas into one expression. Worth the small indirection: two near-identical
# formula sets is where sign errors breed.
CALL, PUT = 1.0, -1.0


@dataclass(frozen=True, slots=True)
class Greeks:
    """Per-contract risk numbers, each an array aligned with the inputs."""

    price: np.ndarray
    delta: np.ndarray
    gamma: np.ndarray
    vega: np.ndarray
    theta: np.ndarray
    rho: np.ndarray

    def as_dict(self) -> dict[str, np.ndarray]:
        return {
            "price": self.price, "delta": self.delta, "gamma": self.gamma,
            "vega": self.vega, "theta": self.theta, "rho": self.rho,
        }


def _d1_d2(
    s: np.ndarray, k: np.ndarray, t: np.ndarray, sigma: np.ndarray, r: float, q: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The shared ``d1``/``d2`` terms, plus ``sigma*sqrt(t)`` which several
    greeks divide by (computed once, and floored so it is never zero)."""
    sqrt_t = np.sqrt(t)
    vol_t = np.maximum(sigma * sqrt_t, MIN_SIGMA)
    d1 = (np.log(s / k) + (r - q + 0.5 * sigma**2) * t) / vol_t
    return d1, d1 - vol_t, vol_t


def price(
    s: np.ndarray, k: np.ndarray, t: np.ndarray, sigma: np.ndarray,
    r: float, q: np.ndarray, phi: np.ndarray,
) -> np.ndarray:
    """Black-Scholes-Merton value. ``phi`` is +1 call / -1 put.

    Expired or zero-vol contracts return intrinsic value, which is the correct
    limit rather than a special case bolted on.
    """
    s, k, t, sigma, q, phi = np.broadcast_arrays(
        *(np.asarray(x, dtype=float) for x in (s, k, t, sigma, q, phi))
    )
    intrinsic = np.maximum(phi * (s - k), 0.0)
    live = (t > MIN_T) & (sigma > MIN_SIGMA) & (s > 0) & (k > 0)

    out = np.array(intrinsic, dtype=float)
    if not live.any():
        return out

    ts = np.where(live, t, 1.0)
    sig = np.where(live, sigma, 0.1)
    d1, d2, _ = _d1_d2(s, k, ts, sig, r, q)
    disc_k = k * np.exp(-r * ts)
    disc_s = s * np.exp(-q * ts)
    value = phi * (disc_s * norm.cdf(phi * d1) - disc_k * norm.cdf(phi * d2))
    return np.where(live, value, out)


def greeks(
    s: np.ndarray, k: np.ndarray, t: np.ndarray, sigma: np.ndarray,
    r: float, q: np.ndarray, phi: np.ndarray,
) -> Greeks:
    """All five greeks plus the model price, in one pass over the arrays.

    Sharing ``d1``/``d2``/``pdf`` across the five outputs is most of why this
    is one function rather than five: recomputing them per greek would quintuple
    the transcendental-function cost, which is the entire runtime here.
    """
    s, k, t, sigma, q, phi = np.broadcast_arrays(
        *(np.asarray(x, dtype=float) for x in (s, k, t, sigma, q, phi))
    )
    live = (t > MIN_T) & (sigma > MIN_SIGMA) & (s > 0) & (k > 0)

    zeros = np.zeros(s.shape, dtype=float)
    ts = np.where(live, t, 1.0)
    sig = np.where(live, sigma, 0.1)

    d1, d2, vol_t = _d1_d2(s, k, ts, sig, r, q)
    pdf = norm.pdf(d1)
    disc_q = np.exp(-q * ts)
    disc_r = np.exp(-r * ts)

    val = phi * (s * disc_q * norm.cdf(phi * d1) - k * disc_r * norm.cdf(phi * d2))

    # Delta: for a put this is N(d1)-1 scaled by the yield discount, which the
    # phi form gives as phi*N(phi*d1) — the same number, without a branch.
    delta = phi * disc_q * norm.cdf(phi * d1)
    gamma = disc_q * pdf / (s * vol_t)
    vega = s * disc_q * pdf * np.sqrt(ts)

    # Theta has three terms: time decay of the option's optionality (always
    # negative), the carry on the discounted strike, and the dividend drag on
    # the discounted spot. Only the first survives for a zero-rate zero-yield
    # option, which is a useful sanity check on the sign.
    theta = (
        -(s * disc_q * pdf * sig) / (2.0 * np.sqrt(ts))
        - phi * r * k * disc_r * norm.cdf(phi * d2)
        + phi * q * s * disc_q * norm.cdf(phi * d1)
    )
    rho = phi * k * ts * disc_r * norm.cdf(phi * d2)

    intrinsic = np.maximum(phi * (s - k), 0.0)
    # An expired option's delta is its moneyness indicator (1 or 0), not zero —
    # that is what a settled in-the-money contract is actually worth per share.
    expired_delta = np.where(phi * (s - k) > 0, phi, 0.0)

    return Greeks(
        price=np.where(live, val, intrinsic),
        delta=np.where(live, delta, expired_delta),
        gamma=np.where(live, gamma, zeros),
        vega=np.where(live, vega, zeros),
        theta=np.where(live, theta, zeros),
        rho=np.where(live, rho, zeros),
    )


def per_point(vega: np.ndarray) -> np.ndarray:
    """Vega per one volatility *point* (1%), the usual display unit."""
    return vega / 100.0


def per_day(theta: np.ndarray) -> np.ndarray:
    """Theta per calendar day, the usual display unit."""
    return theta / 365.0


def no_arbitrage_bounds(
    s: np.ndarray, k: np.ndarray, t: np.ndarray, r: float, q: np.ndarray, phi: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The interval any arbitrage-free option price must lie in.

    Used by the IV solver as a precondition: a quoted price outside these
    bounds has no implied volatility at all — no sigma reproduces it — so the
    solver must reject it rather than converge to a boundary and report a
    number. This is what stops a stale or crossed quote from becoming a
    confident-looking 0.00001 or 5.0 in the database.
    """
    disc_s = s * np.exp(-q * t)
    disc_k = k * np.exp(-r * t)
    lower = np.maximum(phi * (disc_s - disc_k), 0.0)
    upper = np.where(phi > 0, disc_s, disc_k)
    return lower, upper
