"""Chain metrics registry."""

from datetime import datetime, timezone

import numpy as np
import pytest

from app.services.compute.analytics import (
    METRIC_REGISTRY,
    compute_all,
    smile,
    term_structure,
)
from app.services.compute.chain import enrich
from tests.factories import make_chain

NOW = datetime(2026, 8, 21, 14, 35, tzinfo=timezone.utc)


def build_snapshot(**kw):
    chain = make_chain(**kw)
    bid = np.array([q.bid for q in chain.quotes], dtype=float)
    ask = np.array([q.ask for q in chain.quotes], dtype=float)
    return enrich(
        refs=[q.ref for q in chain.quotes], now=NOW, spot=chain.spot,
        bid=bid, ask=ask,
        volume=np.array([q.volume for q in chain.quotes], dtype=float),
        open_interest=np.array([q.open_interest for q in chain.quotes], dtype=float),
    )


def test_every_registered_metric_computes_or_returns_none():
    snap = build_snapshot()
    out = compute_all(snap)
    assert set(out) == set(METRIC_REGISTRY)
    for key, value in out.items():
        assert value is None or np.isfinite(value), key


def test_metrics_never_raise_on_an_empty_chain():
    snap = enrich(refs=[], now=NOW, spot=100.0, bid=np.array([]), ask=np.array([]))
    assert all(v is None for v in compute_all(snap).values())


def test_put_call_ratios():
    snap = build_snapshot()
    out = compute_all(snap)
    # The factory gives calls and puts identical volume and OI.
    assert out["put_call_volume"] == pytest.approx(1.0)
    assert out["put_call_oi"] == pytest.approx(1.0)


def test_max_pain_is_an_actual_listed_strike():
    snap = build_snapshot()
    mp = compute_all(snap)["max_pain"]
    assert mp in set(snap.strike.tolist())


def test_term_structure_is_ordered_and_covers_priced_expiries():
    snap = build_snapshot()
    points = term_structure(snap)
    expiries = [p["expiration"] for p in points]
    assert expiries == sorted(expiries)
    assert len(points) >= 2


def test_smile_is_sorted_by_strike():
    snap = build_snapshot()
    expiry = min(r.expiration for r in snap.refs)
    data = smile(snap, expiry)
    assert data["strike"] == sorted(data["strike"])
    assert len(data["iv"]) == len(data["strike"])


def test_smile_is_empty_for_an_unlisted_expiry():
    from datetime import date

    snap = build_snapshot()
    assert smile(snap, date(2099, 1, 1))["iv"] == []


def test_atm_iv_refuses_to_extrapolate():
    """A chain whose strikes are all far from spot has no ATM vol to read, and
    inventing one by extrapolation would look identical to a real number."""
    snap = build_snapshot(spot=100.0, strikes=(300.0, 310.0, 320.0))
    assert compute_all(snap)["atm_iv"] is None


def test_unpriced_contracts_do_not_poison_aggregates():
    """A single NaN in a sum makes the sum NaN — greeks must be masked, not zeroed."""
    chain = make_chain()
    n = len(chain.quotes)
    bid = np.full(n, np.nan)   # no two-sided quotes at all
    ask = np.full(n, np.nan)
    snap = enrich(
        refs=[q.ref for q in chain.quotes], now=NOW, spot=chain.spot, bid=bid, ask=ask,
        volume=np.ones(n), open_interest=np.full(n, 100.0),
    )
    assert not snap.priced.any()
    out = compute_all(snap)
    assert out["atm_iv"] is None
    # Volume/OI ratios do not depend on pricing, so they still work.
    assert out["put_call_volume"] == pytest.approx(1.0)
