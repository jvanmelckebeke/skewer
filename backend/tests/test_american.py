"""Bjerksund-Stensland 2002, measured rather than trusted.

Every reference value here is computed in the test by a Cox-Ross-Rubinstein
binomial tree. A tree is slow and does not vectorise, which is why skewer does
not price with one, but it converges to the true American value and it is
written from a completely different set of equations. That makes it a real
check rather than a restatement of the thing being tested.
"""

import numpy as np
import pytest

from app.services.compute import american as am
from app.services.compute import bs
from app.services.compute.iv import IVStatus, implied_vol

R = 0.04


def crr(s, k, t, sigma, r, q, phi, steps=1200):
    """American value by a binomial tree. The independent reference."""
    dt = t / steps
    u = np.exp(sigma * np.sqrt(dt))
    d = 1.0 / u
    p = (np.exp((r - q) * dt) - d) / (u - d)
    disc = np.exp(-r * dt)

    j = np.arange(steps + 1)
    st = s * u**j * d ** (steps - j)
    v = np.maximum(phi * (st - k), 0.0)
    for i in range(steps - 1, -1, -1):
        j = np.arange(i + 1)
        st = s * u**j * d ** (i - j)
        v = disc * (p * v[1 : i + 2] + (1.0 - p) * v[0 : i + 1])
        v = np.maximum(v, phi * (st - k))
    return float(v[0])


# Spot, strike, tenor, vol, yield, right. Chosen to span the moneyness and
# tenor range a real chain covers, with and without a dividend.
CASES = [
    (s, 100.0, t, v, q, phi)
    for s in (85.0, 100.0, 115.0)
    for t in (0.1, 0.5, 1.5)
    for v in (0.2, 0.45)
    for q in (0.0, 0.07)
    for phi in (bs.CALL, bs.PUT)
]


@pytest.mark.parametrize("s,k,t,v,q,phi", CASES)
def test_the_price_tracks_a_binomial_tree(s, k, t, v, q, phi):
    """Within 3% on any contract worth more than fifty cents.

    The bound is loose on purpose. Bjerksund-Stensland prices one specific
    sub-optimal exercise policy, so it is a *lower* bound on the true value
    and is expected to sit slightly under the tree. Worst observed across
    these cases is ~2.3%, on a long-dated out-of-the-money put at low
    volatility, which is where the early-exercise boundary is hardest to
    approximate with two steps.
    """
    got = float(am.price(s, k, t, v, R, q, phi))
    want = crr(s, k, t, v, R, q, phi)

    if want < 0.5:
        assert abs(got - want) < 0.02
    else:
        assert abs(got - want) / want < 0.03
    # One-directional: never above the true value.
    assert got <= want + 0.01


@pytest.mark.parametrize("s,k,t,v,q,phi", CASES)
def test_the_price_is_never_below_the_european_one(s, k, t, v, q, phi):
    """The defining property, and the reason the old behaviour was biased."""
    got = float(am.price(s, k, t, v, R, q, phi))
    euro = float(bs.price(s, k, t, v, R, q, phi))

    assert got >= euro - 1e-9
    assert got >= max(phi * (s - k), 0.0) - 1e-9


@pytest.mark.parametrize("s", [80.0, 100.0, 130.0])
@pytest.mark.parametrize("t", [0.1, 1.0])
def test_a_call_on_a_non_payer_is_exactly_european(s, t):
    """Not an approximation: early exercise of a call is never optimal when
    the underlying pays nothing out, so the two values are identical."""
    got = float(am.price(s, 100.0, t, 0.3, R, 0.0, bs.CALL))
    euro = float(bs.price(s, 100.0, t, 0.3, R, 0.0, bs.CALL))

    assert got == pytest.approx(euro, abs=1e-12)


def test_a_dividend_makes_early_exercise_of_a_call_worth_something():
    """The regime the issue names: BMW yields ~7%."""
    plain = float(am.price(100.0, 100.0, 1.0, 0.3, R, 0.07, bs.CALL))
    euro = float(bs.price(100.0, 100.0, 1.0, 0.3, R, 0.07, bs.CALL))

    assert plain > euro


def test_a_put_carries_an_early_exercise_premium_at_a_positive_rate():
    deep = 60.0
    got = float(am.price(deep, 100.0, 1.0, 0.25, R, 0.0, bs.PUT))
    euro = float(bs.price(deep, 100.0, 1.0, 0.25, R, 0.0, bs.PUT))

    assert got > euro
    # It cannot be worth less than exercising today.
    assert got >= 100.0 - deep - 1e-9


def test_the_bounds_are_the_undiscounted_ones():
    """A deep ITM American put trades above the European upper bound, so
    screening it against European bounds would throw away a good quote."""
    s = np.array([60.0])
    k = np.array([100.0])
    t = np.array([1.0])
    q = np.array([0.0])
    phi = np.array([bs.PUT])

    lo, hi = am.no_arbitrage_bounds(s, k, t, R, q, phi)
    euro_lo, euro_hi = bs.no_arbitrage_bounds(s, k, t, R, q, phi)

    assert lo[0] == pytest.approx(40.0)
    assert hi[0] == pytest.approx(100.0)
    assert lo[0] > euro_lo[0]
    assert hi[0] > euro_hi[0]


@pytest.mark.parametrize("sigma", [0.12, 0.3, 0.75])
@pytest.mark.parametrize("q,phi", [(0.0, bs.PUT), (0.07, bs.CALL), (0.0, bs.CALL)])
def test_the_solver_round_trips_an_american_price(sigma, q, phi):
    s, k, t = np.array([100.0]), np.array([100.0]), np.array([0.5])
    quoted = am.price(s, k, t, np.array([sigma]), R, np.array([q]), np.array([phi]))

    res = implied_vol(
        quoted, s, k, t, R, np.array([q]), np.array([phi]), american=np.array([True])
    )

    assert res.status[0] == IVStatus.OK
    assert res.iv[0] == pytest.approx(sigma, abs=1e-6)


def test_solving_an_american_price_as_european_overstates_the_volatility():
    """The bias the issue describes, measured. The European price is a lower
    bound, so reproducing a higher price needs a higher sigma."""
    s, k, t, q, phi = (np.array(x) for x in ([100.0], [100.0], [0.5], [0.0], [bs.PUT]))
    quoted = am.price(s, k, t, np.array([0.30]), R, q, phi)

    right = implied_vol(quoted, s, k, t, R, q, phi, american=np.array([True]))
    wrong = implied_vol(quoted, s, k, t, R, q, phi)

    assert right.iv[0] == pytest.approx(0.30, abs=1e-6)
    assert wrong.iv[0] > right.iv[0]
    # Small, but not negligible: about half a volatility point at the money.
    assert 0.002 < wrong.iv[0] - right.iv[0] < 0.02


def test_a_mixed_chain_solves_each_contract_with_its_own_model():
    """Eurex lists American and European classes on the same underlying at the
    same expiry, so the flag is per contract and not per chain."""
    s = np.array([100.0, 100.0])
    k = np.array([100.0, 100.0])
    t = np.array([0.5, 0.5])
    q = np.array([0.0, 0.0])
    phi = np.array([bs.PUT, bs.PUT])
    quoted = np.array([
        float(am.price(100.0, 100.0, 0.5, 0.3, R, 0.0, bs.PUT)),
        float(bs.price(100.0, 100.0, 0.5, 0.3, R, 0.0, bs.PUT)),
    ])

    res = implied_vol(quoted, s, k, t, R, q, phi, american=np.array([True, False]))

    assert res.iv[0] == pytest.approx(0.30, abs=1e-6)
    assert res.iv[1] == pytest.approx(0.30, abs=1e-6)


def test_the_european_path_is_untouched_when_no_flag_is_given():
    """Every existing caller passes no ``american``, and must behave exactly
    as it did before."""
    s, k, t, q, phi = (np.array(x) for x in ([100.0], [95.0], [0.4], [0.02], [bs.CALL]))
    quoted = bs.price(s, k, t, np.array([0.4]), R, q, phi)

    res = implied_vol(quoted, s, k, t, R, q, phi)

    assert res.iv[0] == pytest.approx(0.4, abs=1e-6)
