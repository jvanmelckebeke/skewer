"""Synthetic chains, so the collection pipeline is testable without a network."""

from datetime import date, datetime, timedelta, timezone

from app.domain.contracts import ContractRef, Right
from app.domain.venue import Venue
from app.services.option_providers.base import RawChain, RawOptionQuote


def make_quote(
    root: str, expiration: date, right: Right, strike: float,
    bid: float | None = 1.0, ask: float | None = 1.2,
    volume: int = 10, open_interest: int | None = 100,
    iv_provider: float | None = 0.35, last: float | None = 1.1,
    settlement: float | None = None,
) -> RawOptionQuote:
    ref = ContractRef.build(root, expiration, right, strike)
    return RawOptionQuote(
        ref=ref, bid=bid, ask=ask, last=last,
        last_trade_at=datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc),
        volume=volume, open_interest=open_interest, iv_provider=iv_provider,
        contract_size=100, currency="USD", settlement=settlement,
    )


def make_chain(
    symbol: str = "TEST", spot: float = 100.0, today: date | None = None,
    dtes: tuple[int, ...] = (3, 20, 60, 400),
    strikes: tuple[float, ...] = (80.0, 90.0, 100.0, 110.0, 120.0),
    fetched_at: datetime | None = None,
) -> RawChain:
    """A chain spanning every cadence bucket, both rights, several strikes."""
    today = today or date(2026, 8, 21)
    fetched_at = fetched_at or datetime(2026, 8, 21, 14, 35, tzinfo=timezone.utc)
    quotes, expirations = [], []
    for dte in dtes:
        exp = today + timedelta(days=dte)
        expirations.append(exp)
        for strike in strikes:
            for right in (Right.CALL, Right.PUT):
                # A plausible two-sided market around a rough intrinsic value.
                intrinsic = max(spot - strike, 0) if right is Right.CALL else max(strike - spot, 0)
                mid = intrinsic + 3.0 + dte * 0.01
                quotes.append(make_quote(symbol, exp, right, strike, bid=mid - 0.1, ask=mid + 0.1))
    return RawChain(
        symbol=symbol, name=f"{symbol} Inc", currency="USD", venue=Venue.OCC, spot=spot,
        bid=spot - 0.05, ask=spot + 0.05, previous_close=spot * 0.99,
        market_state="REGULAR", session_date=today, fetched_at=fetched_at,
        expirations=sorted(set(expirations)), quotes=quotes,
    )
