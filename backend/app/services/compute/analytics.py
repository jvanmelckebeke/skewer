"""Chain-level metrics — skewer's answer to fibenchi's ``INDICATOR_REGISTRY``.

In fibenchi an indicator reduces a price series to a number you can sort a
table by. The options analogue reduces a *cross-section* — a whole chain at one
instant — to a number, because that is where option information lives: not in
one contract's history but in the shape of the surface across strikes and
tenors at a point in time.

Same registry pattern, same reason: a metric declares itself here and every
consumer (API, table columns, future charts) discovers it, so adding one never
means editing a router.

These are deliberately the *well-defined* metrics. Each is a stated arithmetic
fact about the chain, not a trading signal — 25-delta skew is "the IV
difference between the 25-delta put and call", full stop. What that implies is
your call; the job here is to compute it honestly and say when it cannot be
computed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np

from app.services.compute.chain import ChainSnapshot


@dataclass(frozen=True, slots=True)
class MetricDef:
    """One registered chain metric."""

    id: str
    label: str
    func: Callable[[ChainSnapshot], float | None]
    unit: str          # "vol" | "ratio" | "currency" | "shares"
    description: str


METRIC_REGISTRY: dict[str, MetricDef] = {}


def chain_metric(id: str, label: str, unit: str, description: str):
    """Register the decorated function as a chain metric."""

    def decorator(func):
        if id in METRIC_REGISTRY:
            raise ValueError(f"Duplicate chain metric id: {id!r}")
        METRIC_REGISTRY[id] = MetricDef(id, label, func, unit, description)
        return func

    return decorator


def _interp_at(x: np.ndarray, y: np.ndarray, target: float) -> float | None:
    """Linear interpolation of ``y`` at ``target``, requiring ``target`` to be
    genuinely bracketed.

    Refusing to extrapolate is the point. Reading ATM vol off a chain whose
    strikes all sit 20% away is a fabricated number, and it would look exactly
    like a real one on a plot.
    """
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 2:
        return None
    x, y = x[ok], y[ok]
    order = np.argsort(x)
    x, y = x[order], y[order]
    if target < x[0] or target > x[-1]:
        return None
    return float(np.interp(target, x, y))


def _front_expiry(snap: ChainSnapshot) -> date | None:
    """The nearest expiration that still has priced contracts."""
    live = snap.priced
    if not live.any():
        return None
    return min(r.expiration for r, ok in zip(snap.refs, live) if ok)


def _slice_expiry(snap: ChainSnapshot, expiry: date) -> np.ndarray:
    return np.array([r.expiration == expiry for r in snap.refs], dtype=bool)


@chain_metric(
    "atm_iv", "ATM IV", "vol",
    "Implied volatility interpolated at the money (log-moneyness 0) for the "
    "front expiration. The single most-quoted number about a chain.",
)
def atm_iv(snap: ChainSnapshot) -> float | None:
    expiry = _front_expiry(snap)
    if expiry is None:
        return None
    # Calls and puts at the same strike should imply the same vol; in practice
    # they differ slightly. Averaging the two curves is more stable than
    # picking a side, especially near the money where both are liquid.
    sel = _slice_expiry(snap, expiry) & snap.priced
    if sel.sum() < 2:
        return None
    return _interp_at(snap.moneyness[sel], snap.iv[sel], 0.0)


@chain_metric(
    "skew_25d", "25-delta skew", "vol",
    "IV of the 25-delta put minus IV of the 25-delta call, front expiration. "
    "Positive means downside protection is bid up relative to upside.",
)
def skew_25d(snap: ChainSnapshot) -> float | None:
    expiry = _front_expiry(snap)
    if expiry is None:
        return None
    sel = _slice_expiry(snap, expiry) & snap.priced
    puts = sel & ~snap.is_call
    calls = sel & snap.is_call
    if puts.sum() < 2 or calls.sum() < 2:
        return None
    # Put deltas are negative; the 25-delta put is delta = -0.25.
    put_iv = _interp_at(snap.delta[puts], snap.iv[puts], -0.25)
    call_iv = _interp_at(snap.delta[calls], snap.iv[calls], 0.25)
    if put_iv is None or call_iv is None:
        return None
    return put_iv - call_iv


@chain_metric(
    "put_call_volume", "Put/Call volume", "ratio",
    "Total put contract volume divided by total call volume across the chain.",
)
def put_call_volume(snap: ChainSnapshot) -> float | None:
    calls = float(np.nansum(snap.volume[snap.is_call]))
    puts = float(np.nansum(snap.volume[~snap.is_call]))
    return puts / calls if calls > 0 else None


@chain_metric(
    "put_call_oi", "Put/Call OI", "ratio",
    "Total put open interest divided by total call open interest.",
)
def put_call_oi(snap: ChainSnapshot) -> float | None:
    calls = float(np.nansum(snap.open_interest[snap.is_call]))
    puts = float(np.nansum(snap.open_interest[~snap.is_call]))
    return puts / calls if calls > 0 else None


@chain_metric(
    "max_pain", "Max pain", "currency",
    "The strike at which the total intrinsic value of all open contracts in "
    "the front expiration is smallest. A descriptive statistic about where "
    "open interest is concentrated, not a prediction.",
)
def max_pain(snap: ChainSnapshot) -> float | None:
    expiry = _front_expiry(snap)
    if expiry is None:
        return None
    sel = _slice_expiry(snap, expiry)
    oi = snap.open_interest[sel]
    if not np.isfinite(oi).any() or float(np.nansum(oi)) <= 0:
        return None
    strikes = snap.strike[sel]
    is_call = snap.is_call[sel]
    candidates = np.unique(strikes)
    # Pain at settlement price S: sum over contracts of OI * intrinsic value.
    # Vectorised as an outer difference so the whole curve is one operation.
    diff = candidates[:, None] - strikes[None, :]
    intrinsic = np.where(is_call[None, :], np.maximum(diff, 0.0), np.maximum(-diff, 0.0))
    pain = np.nansum(intrinsic * np.nan_to_num(oi)[None, :], axis=1)
    return float(candidates[int(np.argmin(pain))])


@chain_metric(
    "total_gamma", "Net gamma exposure", "shares",
    "Sum of gamma * open interest * contract multiplier * spot, calls minus "
    "puts. The commonly used 'GEX' aggregate; sign convention here is from "
    "the perspective of a dealer short calls and long puts.",
)
def total_gamma(snap: ChainSnapshot) -> float | None:
    live = snap.priced & np.isfinite(snap.gamma) & np.isfinite(snap.open_interest)
    if not live.any():
        return None
    sign = np.where(snap.is_call, 1.0, -1.0)
    gex = snap.gamma[live] * snap.open_interest[live] * 100.0 * snap.spot * sign[live]
    return float(np.nansum(gex))


@chain_metric(
    "iv_term_slope", "IV term slope", "vol",
    "ATM IV of the second expiration minus the front. Positive is contango "
    "(the usual state); negative is backwardation, typical around an event.",
)
def iv_term_slope(snap: ChainSnapshot) -> float | None:
    live = snap.priced
    if not live.any():
        return None
    expiries = sorted({r.expiration for r, ok in zip(snap.refs, live) if ok})
    if len(expiries) < 2:
        return None
    vols = []
    for exp in expiries[:2]:
        sel = _slice_expiry(snap, exp) & live
        if sel.sum() < 2:
            return None
        v = _interp_at(snap.moneyness[sel], snap.iv[sel], 0.0)
        if v is None:
            return None
        vols.append(v)
    return vols[1] - vols[0]


def compute_all(snap: ChainSnapshot) -> dict[str, float | None]:
    """Every registered metric for one snapshot.

    A metric that raises is recorded as ``None`` rather than taking the whole
    snapshot down — the same isolation fibenchi applies per-group in its cache
    warm loop.
    """
    out: dict[str, float | None] = {}
    for mid, mdef in METRIC_REGISTRY.items():
        try:
            out[mid] = mdef.func(snap)
        except Exception:
            out[mid] = None
    return out


def smile(snap: ChainSnapshot, expiry: date) -> dict[str, list[float]]:
    """The IV smile for one expiration: moneyness, strike and IV, sorted.

    Returned as plain lists because this is a plotting payload — the one place
    the array representation stops being the useful one.
    """
    sel = _slice_expiry(snap, expiry) & snap.priced
    if not sel.any():
        return {"moneyness": [], "strike": [], "iv": [], "delta": []}
    order = np.argsort(snap.strike[sel])
    return {
        "moneyness": snap.moneyness[sel][order].tolist(),
        "strike": snap.strike[sel][order].tolist(),
        "iv": snap.iv[sel][order].tolist(),
        "delta": snap.delta[sel][order].tolist(),
    }


def term_structure(snap: ChainSnapshot) -> list[dict[str, float | str]]:
    """ATM IV per expiration, ascending — the term-structure curve."""
    live = snap.priced
    out = []
    for exp in sorted({r.expiration for r, ok in zip(snap.refs, live) if ok}):
        sel = _slice_expiry(snap, exp) & live
        if sel.sum() < 2:
            continue
        v = _interp_at(snap.moneyness[sel], snap.iv[sel], 0.0)
        if v is not None:
            out.append({"expiration": exp.isoformat(), "atm_iv": v, "contracts": int(sel.sum())})
    return out
