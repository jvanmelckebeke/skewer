"""Turn stored quote rows into a priced, greeked chain snapshot.

This is the read-side counterpart to the collector: the fact table stores the
minimum (mark, iv, spot), and everything a user actually looks at is
reconstructed here. Keeping it pure — arrays in, arrays out, no session, no
provider — is what makes it testable and what lets it run identically over a
live snapshot or a slice of history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from app.config import settings
from app.domain.occ import ContractRef, Right
from app.domain.venue import ExerciseStyle
from app.services.compute import american as am
from app.services.compute import bs
from app.services.compute.iv import IVStatus, implied_vol, mid_price


@dataclass(slots=True)
class ChainSnapshot:
    """One simultaneous cross-section of a chain, enriched.

    Every array is aligned with ``refs``. Arrays rather than a list of objects
    because the consumers are all vectorised (surface fits, skew, aggregate
    exposures) and a 3,000-element object list would dominate their runtime.
    """

    ts: datetime
    spot: float
    refs: list[ContractRef]
    strike: np.ndarray
    tenor: np.ndarray          # years to expiry, ACT/365
    is_call: np.ndarray        # bool
    mark: np.ndarray
    iv: np.ndarray
    iv_status: np.ndarray
    volume: np.ndarray
    open_interest: np.ndarray
    delta: np.ndarray = field(default_factory=lambda: np.array([]))
    gamma: np.ndarray = field(default_factory=lambda: np.array([]))
    vega: np.ndarray = field(default_factory=lambda: np.array([]))
    theta: np.ndarray = field(default_factory=lambda: np.array([]))
    rho: np.ndarray = field(default_factory=lambda: np.array([]))
    model_price: np.ndarray = field(default_factory=lambda: np.array([]))

    def __len__(self) -> int:
        return len(self.refs)

    @property
    def priced(self) -> np.ndarray:
        """Mask of contracts with a usable IV — the only ones whose greeks
        mean anything. Every consumer should filter on this rather than
        assuming the whole chain is live."""
        return np.isfinite(self.iv)

    @property
    def moneyness(self) -> np.ndarray:
        """log(K/S) — the natural x-axis for a smile, because it makes the
        skew comparable across expirations and across underlyings in a way
        that a raw strike never is."""
        return np.log(self.strike / self.spot)


def enrich(
    refs: list[ContractRef],
    now: datetime,
    spot: float,
    bid: np.ndarray,
    ask: np.ndarray,
    stored_mark: np.ndarray | None = None,
    stored_iv: np.ndarray | None = None,
    volume: np.ndarray | None = None,
    open_interest: np.ndarray | None = None,
    dividend_yield: float = 0.0,
    rate: float | None = None,
) -> ChainSnapshot:
    """Build an enriched snapshot from raw quote columns.

    ``stored_iv`` short-circuits the solve for rows that already carry a
    persisted IV — the normal read path, since the collector solved it once at
    ingest. Rows without one (an ephemeral, un-persisted fetch) are solved here.

    ``stored_mark`` works the same way, and for a sharper reason. Recomputing
    the mark here means recomputing it from bid and ask alone, and Eurex
    publishes no order book: both are NULL for every European contract, so a
    recomputed mark is NaN across the whole chain. The collector's own
    ``_marks`` falls back to the exchange settlement, and the stored column is
    the result. Reading it back is the only way this side agrees with it.
    """
    n = len(refs)
    rate = settings.risk_free_rate if rate is None else rate
    strike = np.array([r.strike for r in refs], dtype=float)
    tenor = np.array([r.year_fraction(now) for r in refs], dtype=float)
    is_call = np.array([r.right is Right.CALL for r in refs], dtype=bool)
    is_american = np.array(
        [r.exercise_style is ExerciseStyle.AMERICAN for r in refs], dtype=bool
    )
    phi = np.where(is_call, bs.CALL, bs.PUT)

    # Recompute only what was not handed to us. A stored NaN is a real answer
    # (no quote and no settlement) and is left alone; the fallback is for a
    # caller that has no stored column at all.
    computed = mid_price(bid, ask)
    if stored_mark is None:
        mark = computed
    else:
        stored = np.asarray(stored_mark, dtype=float)
        mark = np.where(np.isfinite(stored), stored, computed)

    if stored_iv is None:
        res = implied_vol(
            mark, spot, strike, tenor, rate, dividend_yield, phi, american=is_american
        )
        iv, status = res.iv, res.status
    else:
        iv = np.asarray(stored_iv, dtype=float)
        status = np.where(np.isfinite(iv), IVStatus.OK, IVStatus.NO_QUOTE).astype(object)

    # Greeks are only computed where IV exists. Feeding a NaN sigma through
    # would silently NaN-poison every aggregate downstream (a single NaN in a
    # sum makes the sum NaN), so unpriced rows get a placeholder sigma and are
    # masked back out afterwards.
    safe_iv = np.where(np.isfinite(iv), iv, 0.0)
    g = bs.greeks(spot, strike, tenor, safe_iv, rate, dividend_yield, phi)
    live = np.isfinite(iv)
    blank = lambda arr: np.where(live, arr, np.nan)  # noqa: E731

    # The model price uses the right exercise style; the greeks do not.
    #
    # That split is deliberate rather than an oversight. Both prices are one
    # array op, so pricing correctly costs nothing. American greeks have no
    # closed form under Bjerksund-Stensland and would have to be finite
    # differenced, which is ten more price evaluations on every chain read.
    # The greeks are therefore the European sensitivities *at the American
    # implied volatility*, which is the standard practical compromise and is
    # closest to wrong for deep in-the-money puts. Stated here so nobody has
    # to rediscover it from a delta that disagrees with the price.
    model_price = am.price(spot, strike, tenor, safe_iv, rate, dividend_yield, phi)

    return ChainSnapshot(
        ts=now, spot=spot, refs=refs, strike=strike, tenor=tenor, is_call=is_call,
        mark=mark, iv=iv, iv_status=status,
        volume=np.zeros(n) if volume is None else np.asarray(volume, dtype=float),
        open_interest=np.zeros(n) if open_interest is None else np.asarray(open_interest, dtype=float),
        delta=blank(g.delta), gamma=blank(g.gamma), vega=blank(g.vega),
        theta=blank(g.theta), rho=blank(g.rho), model_price=blank(model_price),
    )
