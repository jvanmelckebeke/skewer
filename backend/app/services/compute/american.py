"""American option pricing: Bjerksund-Stensland (2002), vectorised.

Why this exists
---------------

Both venues skewer collects list American-style equity options, and until this
module landed every one of them was priced with Black-Scholes-Merton, which is
a European model. The error is one-directional: an American option is worth at
least its European counterpart, because the holder can do everything the
European holder can and may also exercise early. So the European price is a
lower bound, and a volatility implied against it is biased.

Where it actually costs something:

* **Puts**, always. The right to exercise early and collect the strike is worth
  real money whenever rates are positive, and most of it for deep in-the-money
  strikes.
* **Calls on names that pay a dividend.** With ``q = 0`` early exercise of a
  call is never optimal, and this function returns the European price exactly.
  BMW yields ~7%, which is squarely the regime where it is.

Why Bjerksund-Stensland rather than a tree
------------------------------------------

It is closed-form, so it vectorises over a whole chain the way the rest of
``compute/`` does. A binomial tree is a loop over time steps and would have to
run per contract; a 3,000-contract chain solved by bisection evaluates the
price ~60 times, which makes the difference between milliseconds and minutes.

The 2002 refinement uses a two-step exercise boundary rather than the flat one
of the 1993 paper, which is what brings it within a fraction of a percent of a
converged tree over the moneyness range an option chain actually spans. It is
an *approximation*, and a lower bound on the true American value: the formula
prices a specific sub-optimal exercise strategy, so it can never overstate.
That direction matters, since it is the same direction as the error it
replaces, only far smaller. The tests measure it against a 2,000-step
Cox-Ross-Rubinstein tree rather than trusting the citation.

Notation follows the paper: ``b`` is the cost of carry, ``r - q``.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm

from app.services.compute.bs import MIN_SIGMA, MIN_T
from app.services.compute.bs import price as european_price

# The 2002 paper's split of the option's life into two exercise regions.
# t1 = (sqrt(5) - 1) / 2 * T is the paper's choice, not a tunable.
_T1_FRACTION = 0.5 * (np.sqrt(5.0) - 1.0)

# Gauss-Legendre nodes for the bivariate normal integral below. The
# correlation is fixed at +/- sqrt(_T1_FRACTION) ~ 0.786, comfortably away
# from the +/-1 singularity, so a modest rule is exact to well beyond the cent
# that option prices are quoted in. Ten nodes costs ~1e-10 absolute; five
# would do, and the margin is cheap.
_GL_NODES, _GL_WEIGHTS = np.polynomial.legendre.leggauss(10)


def _bivariate_normal_cdf(h: np.ndarray, k: np.ndarray, rho: float) -> np.ndarray:
    """P(X <= h, Y <= k) for standard bivariate normals with correlation rho.

    Uses the tetrachoric integral

        M(h, k, rho) = N(h)N(k) + (1/2pi) * INT_0^rho
                       exp(-(h^2 - 2 x h k + k^2) / (2(1 - x^2))) / sqrt(1-x^2) dx

    evaluated by Gauss-Legendre. scipy's multivariate normal CDF is not
    vectorised over its arguments, and calling it per contract would undo the
    entire reason this module is closed-form.
    """
    h, k = np.broadcast_arrays(np.asarray(h, dtype=float), np.asarray(k, dtype=float))
    base = norm.cdf(h) * norm.cdf(k)
    if rho == 0.0:
        return base

    # Map the nodes from [-1, 1] onto [0, rho], adding a trailing axis so the
    # whole quadrature happens in one broadcast.
    half = 0.5 * rho
    x = half * (_GL_NODES + 1.0)
    w = half * _GL_WEIGHTS

    hh = h[..., None]
    kk = k[..., None]
    denom = 1.0 - x**2
    integrand = np.exp(-(hh**2 - 2.0 * x * hh * kk + kk**2) / (2.0 * denom)) / np.sqrt(denom)
    return base + (integrand * w).sum(axis=-1) / (2.0 * np.pi)


def _phi(
    s: np.ndarray, t: np.ndarray, gamma: np.ndarray, h: np.ndarray, x: np.ndarray,
    r: float, b: np.ndarray, v: np.ndarray,
) -> np.ndarray:
    """The paper's ``phi``: a down-and-out style term on the flat boundary."""
    v2 = v**2
    sqrt_t = v * np.sqrt(t)
    lam = (-r + gamma * b + 0.5 * gamma * (gamma - 1.0) * v2) * t
    d = -(np.log(s / h) + (b + (gamma - 0.5) * v2) * t) / sqrt_t
    kappa = 2.0 * b / v2 + (2.0 * gamma - 1.0)
    return np.exp(lam) * s**gamma * (
        norm.cdf(d) - (x / s) ** kappa * norm.cdf(d - 2.0 * np.log(x / s) / sqrt_t)
    )


def _psi(
    s: np.ndarray, t: np.ndarray, gamma: np.ndarray, h: np.ndarray,
    x2: np.ndarray, x1: np.ndarray, t1: np.ndarray,
    r: float, b: np.ndarray, v: np.ndarray,
) -> np.ndarray:
    """The paper's ``psi``: the two-boundary term, hence the bivariate CDF."""
    v2 = v**2
    drift = b + (gamma - 0.5) * v2
    s1 = v * np.sqrt(t1)
    s2 = v * np.sqrt(t)

    e1 = (np.log(s / x1) + drift * t1) / s1
    e2 = (np.log(x2**2 / (s * x1)) + drift * t1) / s1
    e3 = (np.log(s / x1) - drift * t1) / s1
    e4 = (np.log(x2**2 / (s * x1)) - drift * t1) / s1

    f1 = (np.log(s / h) + drift * t) / s2
    f2 = (np.log(x2**2 / (s * h)) + drift * t) / s2
    f3 = (np.log(x1**2 / (s * h)) + drift * t) / s2
    f4 = (np.log(s * x1**2 / (h * x2**2)) + drift * t) / s2

    rho = float(np.sqrt(_T1_FRACTION))
    lam = -r + gamma * b + 0.5 * gamma * (gamma - 1.0) * v2
    kappa = 2.0 * b / v2 + (2.0 * gamma - 1.0)

    return np.exp(lam * t) * s**gamma * (
        _bivariate_normal_cdf(-e1, -f1, rho)
        - (x2 / s) ** kappa * _bivariate_normal_cdf(-e2, -f2, rho)
        - (x1 / s) ** kappa * _bivariate_normal_cdf(-e3, -f3, -rho)
        + (x1 / x2) ** kappa * _bivariate_normal_cdf(-e4, -f4, -rho)
    )


def _american_call(
    s: np.ndarray, k: np.ndarray, t: np.ndarray, v: np.ndarray, r: float, b: np.ndarray
) -> np.ndarray:
    """BS2002 for a call. Puts reach this through the transformation below."""
    v2 = np.maximum(v**2, MIN_SIGMA)
    t1 = _T1_FRACTION * t

    beta = (0.5 - b / v2) + np.sqrt((b / v2 - 0.5) ** 2 + 2.0 * r / v2)
    # beta = 1 makes b_inf singular. It happens only when r == 0 and b == 0,
    # where early exercise has no value anyway and the European price is the
    # answer, so nudging it away is safe rather than a fudge.
    beta = np.where(np.abs(beta - 1.0) < 1e-8, 1.0 + 1e-8, beta)

    b_inf = beta / (beta - 1.0) * k
    # r == b means the carry never makes exercise worthwhile. The caller has
    # already excluded those, but the divisor is floored anyway rather than
    # left to raise a warning on an element whose result is discarded.
    gap = r - b
    gap = np.where(np.abs(gap) < 1e-12, 1e-12, gap)
    b_zero = np.maximum(k, r / gap * k)

    spread = np.maximum(b_inf - b_zero, 1e-12)
    ht1 = -(b * t1 + 2.0 * v * np.sqrt(t1)) * k**2 / (spread * b_zero)
    ht2 = -(b * t + 2.0 * v * np.sqrt(t)) * k**2 / (spread * b_zero)
    i1 = b_zero + (b_inf - b_zero) * (1.0 - np.exp(ht1))
    i2 = b_zero + (b_inf - b_zero) * (1.0 - np.exp(ht2))

    alpha1 = (i1 - k) * i1 ** (-beta)
    alpha2 = (i2 - k) * i2 ** (-beta)

    one = np.ones_like(s)
    zero = np.zeros_like(s)

    value = (
        alpha2 * s**beta
        - alpha2 * _phi(s, t1, beta, i2, i2, r, b, v)
        + _phi(s, t1, one, i2, i2, r, b, v)
        - _phi(s, t1, one, i1, i2, r, b, v)
        - k * _phi(s, t1, zero, i2, i2, r, b, v)
        + k * _phi(s, t1, zero, i1, i2, r, b, v)
        + alpha1 * _phi(s, t1, beta, i1, i2, r, b, v)
        - alpha1 * _psi(s, t, beta, i1, i2, i1, t1, r, b, v)
        + _psi(s, t, one, i1, i2, i1, t1, r, b, v)
        - _psi(s, t, one, k, i2, i1, t1, r, b, v)
        - k * _psi(s, t, zero, i1, i2, i1, t1, r, b, v)
        + k * _psi(s, t, zero, k, i2, i1, t1, r, b, v)
    )

    # Above the upper boundary the holder exercises immediately.
    return np.where(s >= i2, s - k, value)


def price(
    s: np.ndarray, k: np.ndarray, t: np.ndarray, sigma: np.ndarray,
    r: float, q: np.ndarray, phi: np.ndarray,
) -> np.ndarray:
    """American option value. Same signature as :func:`bs.price`.

    ``phi`` is +1 call / -1 put, as everywhere else in ``compute/``.

    Two cases short-circuit to the European price, and neither is an
    approximation:

    * **Calls with ``b >= r``** (equivalently ``q <= 0``). Early exercise of a
      call is never optimal when the underlying pays nothing out, so the
      American and European values are equal. This is most of the OCC chain.
    * Expired or zero-vol contracts, which are worth intrinsic either way.

    Puts are priced through the Bjerksund-Stensland transformation
    ``P(S, K, T, r, b, v) = C(K, S, T, r - b, -b, v)``, which is exact, not an
    approximation: it is the same formula read from the other side of the
    exchange.
    """
    s, k, t, sigma, q, phi = np.broadcast_arrays(
        *(np.asarray(x, dtype=float) for x in (s, k, t, sigma, q, phi))
    )
    euro = european_price(s, k, t, sigma, r, q, phi)
    live = (t > MIN_T) & (sigma > MIN_SIGMA) & (s > 0) & (k > 0)
    if not live.any():
        return euro

    b = r - q
    ts = np.where(live, t, 1.0)
    sig = np.where(live, sigma, 0.1)
    ss = np.where(live, s, 1.0)
    kk = np.where(live, k, 1.0)

    is_call = phi > 0
    # Calls: the carry condition. Puts: always priced with the transformation,
    # since the right to collect the strike early has value at any positive r.
    early = live & np.where(is_call, b < r, True)
    if not early.any():
        return euro

    # Evaluated only on the contracts that need it, and calls and puts
    # separately. On a chain with no dividend every call short-circuits, and
    # the formula is ~80x the cost of the European one, so the subsetting is
    # worth the index arithmetic rather than being premature.
    b_arr = np.broadcast_to(np.asarray(b, dtype=float), s.shape)
    american = np.array(euro, dtype=float)

    for side, want_call in ((is_call, True), (~is_call, False)):
        mask = early & side
        if not mask.any():
            continue
        sm, km = ss[mask], kk[mask]
        tm, vm, bm = ts[mask], sig[mask], b_arr[mask]
        # Two regimes produce non-finite intermediates, both of them ones
        # where early exercise is worth nothing and the floors below give the
        # right answer anyway. `beta` grows like 1/sigma^2, so at the solver's
        # SIGMA_LO of 1% the `s ** beta` terms overflow and cancel to NaN; and
        # a chain's cheapest strikes reach 0.01, where the boundary ratios
        # take log of zero. Both are expected here rather than symptoms of a
        # bad input, so they are silenced and the result is screened for
        # finiteness instead.
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            if want_call:
                value = _american_call(sm, km, tm, vm, r, bm)
            else:
                # The put transformation, exact rather than approximate: swap
                # spot and strike, and swap the roles of rate and carry.
                value = _american_call(km, sm, tm, vm, r - bm, -bm)
        american[mask] = np.where(np.isfinite(value), value, 0.0)

    # The formula prices a specific exercise strategy, so it is a lower bound
    # on the true value — but an approximate one, and floating point can put
    # it a hair under a bound it can never really break. The European price
    # and intrinsic are both hard floors.
    intrinsic = np.maximum(phi * (s - k), 0.0)
    american = np.maximum(np.maximum(american, euro), intrinsic)
    return np.where(early, american, euro)


def no_arbitrage_bounds(
    s: np.ndarray, k: np.ndarray, t: np.ndarray, r: float, q: np.ndarray, phi: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The interval an American option price must lie in.

    Wider than the European one at both ends, and it has to be: the lower
    bound is *undiscounted* intrinsic, because the holder can take it today,
    and the upper bound is the undiscounted spot (calls) or strike (puts) for
    the same reason. Using the European bounds on an American quote would
    reject legitimately priced deep in-the-money contracts as violating a
    bound they are not subject to.
    """
    lower = np.maximum(phi * (s - k), 0.0)
    upper = np.where(phi > 0, s, k)
    return lower, upper
