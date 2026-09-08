"""Black-Scholes pricing, greeks and the IV solver.

The IV tests are the important ones. The whole reason skewer solves its own
implied volatility is that the provider's solver silently reports its bracket
edge when the price carries no volatility information — so these tests pin the
behaviour that must *not* regress into that.
"""

import numpy as np
import pytest

from app.services.compute.bs import CALL, PUT, greeks, no_arbitrage_bounds, per_day, per_point, price
from app.services.compute.iv import IVStatus, implied_vol, mid_price

S, K, T, SIG, R, Q = 100.0, 95.0, 0.75, 0.28, 0.045, 0.015


def test_matches_a_published_reference():
    """Hull, S=42 K=40 r=0.10 sigma=0.20 T=0.5 -> call 4.76, put 0.81."""
    assert float(price(42, 40, 0.5, 0.20, 0.10, 0.0, CALL)) == pytest.approx(4.76, abs=0.01)
    assert float(price(42, 40, 0.5, 0.20, 0.10, 0.0, PUT)) == pytest.approx(0.81, abs=0.01)


def test_put_call_parity():
    c = float(price(S, K, T, SIG, R, Q, CALL))
    p = float(price(S, K, T, SIG, R, Q, PUT))
    assert c - p == pytest.approx(S * np.exp(-Q * T) - K * np.exp(-R * T), abs=1e-10)


@pytest.mark.parametrize("name,bump,order", [
    ("delta", "s", 1), ("vega", "sig", 1), ("rho", "r", 1), ("gamma", "s", 2),
])
def test_greeks_match_finite_differences(name, bump, order):
    h = 1e-5
    g = getattr(greeks(S, K, T, SIG, R, Q, CALL), name)

    def px(**kw):
        args = {"s": S, "k": K, "t": T, "sig": SIG, "r": R, "q": Q}
        args.update(kw)
        return float(price(args["s"], args["k"], args["t"], args["sig"], args["r"], args["q"], CALL))

    base = {"s": S, "sig": SIG, "r": R}[bump]
    up, down = px(**{bump: base + h}), px(**{bump: base - h})
    if order == 1:
        fd = (up - down) / (2 * h)
    else:
        fd = (up - 2 * px() + down) / h**2
    assert float(g) == pytest.approx(fd, rel=1e-3, abs=1e-4)


def test_theta_is_negative_for_a_long_option():
    assert float(greeks(S, K, T, SIG, R, Q, CALL).theta) < 0


def test_call_and_put_share_gamma_and_vega():
    c = greeks(S, K, T, SIG, R, Q, CALL)
    p = greeks(S, K, T, SIG, R, Q, PUT)
    assert float(c.gamma) == pytest.approx(float(p.gamma))
    assert float(c.vega) == pytest.approx(float(p.vega))


def test_delta_bounds():
    strikes = np.linspace(1, 400, 500)
    for phi in (CALL, PUT):
        d = greeks(S, strikes, T, SIG, R, Q, phi).delta
        assert np.all(d <= 1.0 + 1e-12) and np.all(d >= -1.0 - 1e-12)


def test_expired_option_is_worth_intrinsic_and_greeks_stay_finite():
    assert float(price(110, 100, 0.0, 0.3, 0.04, 0.0, CALL)) == 10.0
    assert float(price(110, 100, 0.0, 0.3, 0.04, 0.0, PUT)) == 0.0
    g = greeks(110, 100, 0.0, 0.3, 0.04, 0.0, CALL)
    assert float(g.delta) == 1.0
    assert all(np.isfinite(float(v)) for v in (g.gamma, g.vega, g.theta, g.rho))


def test_zero_vol_is_intrinsic():
    assert float(price(110, 100, 0.5, 0.0, 0.0, 0.0, CALL)) == pytest.approx(10.0)


def test_display_unit_helpers():
    assert per_point(np.array([100.0]))[0] == 1.0
    assert per_day(np.array([365.0]))[0] == 1.0


# --- implied volatility -------------------------------------------------

@pytest.mark.parametrize("true_sigma", [0.05, 0.15, 0.35, 0.8, 2.0, 4.5])
def test_iv_round_trips(true_sigma):
    p = price(S, 100.0, T, true_sigma, R, Q, CALL)
    res = implied_vol(p, S, 100.0, T, R, Q, CALL)
    assert float(res.iv) == pytest.approx(true_sigma, abs=1e-8)
    assert res.status.item() == IVStatus.OK


def test_iv_refuses_when_vega_is_negligible():
    """The regression guard for the provider's 1e-05 artefact.

    A deep in-the-money option is ~all intrinsic: every sigma in the bracket
    reproduces its price, so a converged solver returns whichever edge it drifted
    to. The correct answer is no answer.
    """
    p = price(S, 2.5, T, 0.45, R, Q, CALL)
    res = implied_vol(p, S, 2.5, T, R, Q, CALL)
    assert np.isnan(float(res.iv))
    assert res.status.item() == IVStatus.NO_VEGA


@pytest.mark.parametrize("bad,expected", [
    (0.0, IVStatus.NO_QUOTE),
    (-1.0, IVStatus.NO_QUOTE),
    (float("nan"), IVStatus.NO_QUOTE),
    (1e9, IVStatus.ABOVE_BOUND),
])
def test_iv_rejects_unusable_prices(bad, expected):
    res = implied_vol(bad, S, K, T, R, Q, CALL)
    assert np.isnan(float(res.iv))
    assert res.status.item() == expected


def test_iv_rejects_expired():
    res = implied_vol(5.0, S, K, 0.0, R, Q, CALL)
    assert np.isnan(float(res.iv))
    assert res.status.item() == IVStatus.EXPIRED


def test_iv_rejects_price_below_intrinsic():
    """12% of a real NVDA chain quotes a lastPrice below intrinsic, because the
    'last' trade was weeks ago."""
    res = implied_vol(1.0, 200.0, 100.0, 0.5, R, 0.0, CALL)
    assert np.isnan(float(res.iv))
    assert res.status.item() == IVStatus.BELOW_INTRINSIC


def test_iv_solves_a_whole_chain_accurately():
    n = 500
    strikes = np.linspace(60, 160, n)
    phi = np.where(np.arange(n) % 2 == 0, CALL, PUT)
    sig = np.random.default_rng(0).uniform(0.15, 1.2, n)
    p = price(S, strikes, T, sig, R, Q, phi)
    res = implied_vol(p, S, strikes, T, R, Q, phi)
    ok = res.solved
    assert ok.sum() > n * 0.5
    assert np.max(np.abs(res.iv[ok] - sig[ok])) < 1e-8


def test_mid_price_treats_provider_zeros_as_no_quote():
    """Yahoo returns bid=0/ask=0 for most contracts outside regular hours.
    A zero bid is 'nobody is quoting', not 'worth nothing'."""
    bid = np.array([0.0, 1.0, 0.0, 5.0, 5.0])
    ask = np.array([0.0, 1.2, 3.0, 4.0, 5.2])
    mid = mid_price(bid, ask)
    assert np.isnan(mid[0])        # both zero
    assert mid[1] == pytest.approx(1.1)
    assert np.isnan(mid[2])        # one-sided
    assert np.isnan(mid[3])        # crossed (ask < bid)
    assert mid[4] == pytest.approx(5.1)


def test_no_arbitrage_bounds_contain_the_model_price():
    strikes = np.linspace(10, 300, 200)
    for phi in (CALL, PUT):
        p = price(S, strikes, T, SIG, R, Q, phi)
        lo, hi = no_arbitrage_bounds(S, strikes, T, R, Q, phi)
        assert np.all(p >= lo - 1e-9) and np.all(p <= hi + 1e-9)
