"""Yahoo Finance option chains, read from the raw JSON payload.

Deliberately **not** using ``yahooquery``'s ``option_chain`` DataFrame
property. That helper calls ``df.fillna(0, inplace=True)``, which erases the
distinction between "Yahoo reported no bid" and "Yahoo reported a bid of
zero" — and since Yahoo itself returns ``bid: 0.0`` for most contracts outside
regular hours, a downstream consumer cannot recover the difference. It also
drops the ``quote`` block, which carries spot, market state and the exchange
timezone in the *same response* as the chain.

So we call ``Ticker._get_data("options", {"getAllData": True})`` and normalise
ourselves. One request per underlying returns every expiration — verified
against the live API: NVDA came back as 3,286 contracts across 20 expirations
in a single round-trip. That is why the capture strategy varies *what is
persisted* by tenor rather than what is requested (see ``domain/cadence.py``).

Known limitations, accepted for now:

* **US listings only.** Yahoo publishes no option chains for European tickers.
* **Delayed data.** Quotes are typically 15 minutes delayed for equities.
* **``lastPrice`` is not a price.** It is the last trade, whenever that was;
  12% of a real NVDA chain quoted a ``lastPrice`` *below intrinsic value*
  because the trade was weeks old. It is stored with its timestamp and never
  used as a pricing basis.
* **``impliedVolatility`` is unreliable.** 49% of a real NVDA chain came back
  at 1e-05 (the provider's solver failing), with outliers up to 18.0. Stored
  for comparison only; skewer solves its own (``compute/iv.py``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

from yahooquery import Ticker

from app.domain.contracts import ContractRef
from app.domain.occ import OccParseError
from app.domain.venue import Venue
from app.services.option_providers.base import (
    ChainUnavailable,
    OptionProvider,
    RawChain,
    RawOptionQuote,
)
from app.services.option_providers.yahoo_rate_limit import (
    CrumbRejected,
    YahooThrottle,
    check_crumb,
    yahoo_throttle,
)

logger = logging.getLogger(__name__)

# How long one ``Ticker`` (its curl_cffi session, crumb and consent cookies)
# lives before a fresh one is built. Reuse skips the consent + crumb bootstrap
# on every call after the first; the refresh rotates the browser fingerprint
# and renews the crumb before Yahoo expires it. Same value as fibenchi's.
SESSION_TTL = 600.0

_T = TypeVar("_T")


class YahooThrottled(RuntimeError):
    """Yahoo's circuit breaker is open, so no request was sent.

    Deliberately not :class:`ChainUnavailable`: that means "this symbol has no
    options", and validation would turn a temporary IP block into a permanent
    rejection of a perfectly good underlying.
    """


def _throttled() -> Any:
    raise YahooThrottled("Yahoo is rate-limiting this IP; request skipped")


# Yahoo epochs are seconds UTC.
def _ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, OSError, TypeError):
        return None


def _num(value) -> float | None:
    """A provider number, or None.

    Zero maps to None for price fields. This is the single most consequential
    line in the module: Yahoo returns ``bid: 0.0``/``ask: 0.0`` for the
    overwhelming majority of contracts whenever the market is not in regular
    session (2,948 of NVDA's 3,286 while in PRE). A zero bid means "nobody is
    quoting", not "this is worth nothing", and letting a 0.0 through would put
    a zero mark and a fabricated IV on most of the chain every single night.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f <= 0.0:
        return None
    return f


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# Yahoo's contractSize is a label, not a number. Skewer stores the integer
# multiplier because that is what every venue agrees on; Eurex has no notion of
# 'REGULAR'. Anything unrecognised falls back to 100, the standard equity
# option multiplier on both venues skewer supports.
_CONTRACT_SIZES = {"REGULAR": 100, "MINI": 10}


class YahooOptionProvider(OptionProvider):
    """Option chains via Yahoo Finance — US listings only.

    Yahoo serves OPRA, so its option payload covers US-listed contracts and
    nothing else. Asking it for BMW.DE returns a perfectly good *stock* quote
    with no options attached, which is why :class:`EurexOptionProvider` exists
    and why this one is registered against ``Venue.OCC`` alone.
    """

    venue = Venue.OCC
    intraday = True

    def __init__(
        self,
        throttle: YahooThrottle | None = None,
        session_ttl: float = SESSION_TTL,
    ) -> None:
        self._throttle = throttle or yahoo_throttle
        self._session_ttl = session_ttl
        # Held across "point the shared Ticker at these symbols" *and* the
        # request itself. The Ticker is one mutable object, so a validation
        # call from the API landing mid-poll would otherwise retarget it under
        # the chain loop's feet.
        self._session_lock = threading.Lock()
        self._cached_ticker: Any | None = None
        self._session_created_at = 0.0

    def _ticker(self, symbols: list[str]) -> Any:
        """The shared ``Ticker``, pointed at ``symbols``. Call under the lock."""
        now = time.monotonic()
        if self._cached_ticker is None or now - self._session_created_at > self._session_ttl:
            self._cached_ticker = Ticker(symbols, asynchronous=False, validate=False)
            self._session_created_at = now
        else:
            self._cached_ticker.symbols = symbols
        return self._cached_ticker

    def _invalidate_session(self) -> None:
        with self._session_lock:
            self._cached_ticker = None
            self._session_created_at = 0.0

    def _call(self, fetch: Callable[[], _T], fallback: Callable[[], _T], *, retries: int = 1) -> _T:
        """Run ``fetch`` under the throttle and circuit breaker.

        Same policy as fibenchi's ``YahooClient._call``: an open breaker
        returns ``fallback()`` without touching the network; a crumb rejection
        retries once on a fresh session, then trips the breaker. Any other
        exception propagates so a transient error is not mistaken for a block.
        """
        if self._throttle.is_blocked():
            return fallback()

        for attempt in range(retries + 1):
            self._throttle.wait()
            try:
                with self._session_lock:
                    result = fetch()
            except CrumbRejected:
                self._invalidate_session()
                if attempt < retries:
                    logger.warning(
                        "Yahoo crumb rejected, retry %d/%d with a fresh session",
                        attempt + 1, retries,
                    )
                    continue
                self._throttle.record_invalid_crumb()
                return fallback()
            self._throttle.record_success()
            return result

        return fallback()  # unreachable; quiets the type checker

    async def fetch_spot(self, symbol: str) -> dict | None:
        """The underlying's own quote block, without its option chain.

        Split out because the Eurex provider needs it: Deutsche Boerse's free
        feed publishes option terms and settlement prices but no underlying
        spot, and an option surface without a spot has no moneyness axis and
        therefore no implied volatility. Yahoo does serve European *equity*
        quotes (BMW.DE comes back in EUR off XETRA), so the two feeds compose:
        Eurex for the surface, Yahoo for the spot it is measured against.
        """
        try:
            data = await asyncio.to_thread(self._fetch_price, symbol)
        except Exception:
            logger.warning("Spot fetch failed for %s", symbol, exc_info=True)
            return None
        return data

    async def fetch_close(self, symbol: str, on: date) -> float | None:
        """The underlying's closing price for a session, or the nearest before it.

        Needed by the Eurex provider, and the reason is a correctness one that
        bit immediately when it was missing. Eurex publishes *settlement*
        prices for a session that closed yesterday. Pricing those against
        today's live spot mixes two different moments, and the error is not
        cosmetic: on a live BMW chain it pushed 252 of 952 contracts below
        their own no-arbitrage lower bound, because the stock had moved
        overnight and deep in-the-money options priced off yesterday's close
        now looked cheaper than intrinsic. The 631 that still solved were
        solving against the wrong spot too, just less visibly.

        Falls back to the most recent close at or before ``on`` rather than
        returning nothing, because the two feeds do not always agree on which
        days exist — Yahoo had no 2026-09-07 bar for BMW.DE on a session Eurex
        settled. A close from one session earlier is an approximation and is
        far better than no pricing basis at all, but it *is* an
        approximation.
        """
        try:
            close = await asyncio.to_thread(self._fetch_close, symbol, on)
        except Exception:
            logger.warning("Close lookup failed for %s on %s", symbol, on, exc_info=True)
            return None
        return close

    def _fetch_close(self, symbol: str, on: date) -> float | None:
        # A 10-day window comfortably spans a long weekend plus a holiday,
        # which is what it takes to be sure of finding a prior session.
        start = on - timedelta(days=10)

        def fetch():
            frame = self._ticker([symbol]).history(
                start=start.isoformat(), end=(on + timedelta(days=1)).isoformat(), interval="1d"
            )
            check_crumb(frame)
            return frame

        frame = self._call(fetch, _throttled)
        if not hasattr(frame, "empty") or frame.empty or "close" not in frame:
            return None

        best: tuple[date, float] | None = None
        for index, value in frame["close"].items():
            # The frame is indexed by (symbol, date) for a single ticker too.
            raw = index[-1] if isinstance(index, tuple) else index
            day = getattr(raw, "date", lambda: raw)()
            if not isinstance(day, date) or day > on:
                continue
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            if price > 0 and (best is None or day > best[0]):
                best = (day, price)
        return None if best is None else best[1]

    def _fetch_price(self, symbol: str) -> dict | None:
        # ``quotes`` is the /v7 quote endpoint, one request however many
        # symbols; ``price`` fans out one quoteSummary request per symbol. It
        # also carries bid/ask, which the quoteSummary price module does not.
        def fetch():
            data = self._ticker([symbol]).quotes
            check_crumb(data)
            return data

        quotes = self._call(fetch, _throttled)
        if not isinstance(quotes, dict):
            return None
        block = quotes.get(symbol) or quotes.get(symbol.upper())
        return block if isinstance(block, dict) else None

    async def fetch_chain(self, symbol: str, product: str | None = None) -> RawChain:
        chains = await self.fetch_chains([symbol])
        chain = chains.get(symbol.upper())
        if chain is None:
            raise ChainUnavailable(f"No option chain available for {symbol!r}")
        return chain

    async def fetch_chains(self, symbols: list[str]) -> dict[str, RawChain]:
        if not symbols:
            return {}
        # yahooquery is synchronous and network-bound; a thread keeps the event
        # loop free for the rest of the app during the multi-second fetch.
        raw = await asyncio.to_thread(self._fetch_raw, symbols)
        fetched_at = datetime.now(tz=timezone.utc)

        out: dict[str, RawChain] = {}
        for symbol in symbols:
            payload = raw.get(symbol.upper()) or raw.get(symbol)
            if not isinstance(payload, dict):
                logger.warning("No option payload for %s (Yahoo returned %r)", symbol, type(payload).__name__)
                continue
            try:
                chain = self._parse_chain(symbol.upper(), payload, fetched_at)
            except Exception:
                logger.warning("Failed to parse option chain for %s", symbol, exc_info=True)
                continue
            if chain is not None:
                out[chain.symbol] = chain
        return out

    def _fetch_raw(self, symbols: list[str]) -> dict:
        def fetch():
            data = self._ticker(symbols)._get_data("options", {"getAllData": True})
            check_crumb(data)
            return data

        data = self._call(fetch, _throttled)
        # A single-symbol request can come back unwrapped.
        if isinstance(data, dict) and "options" in data and len(symbols) == 1:
            return {symbols[0].upper(): data}
        return data if isinstance(data, dict) else {}

    def _parse_chain(self, symbol: str, payload: dict, fetched_at: datetime) -> RawChain | None:
        groups = payload.get("options")
        if not groups:
            return None

        quote = payload.get("quote") or {}
        market_time = _ts(quote.get("regularMarketTime"))
        # The session date must be the *exchange-local* date. The EOD snapshot
        # runs at 21:10 UTC, which is already the following calendar day in
        # much of the world — dating those rows in UTC would file a Friday
        # session under Saturday.
        session_date = None
        if market_time is not None:
            tz_name = quote.get("exchangeTimezoneName")
            try:
                from zoneinfo import ZoneInfo

                session_date = market_time.astimezone(ZoneInfo(tz_name)).date() if tz_name else market_time.date()
            except Exception:
                session_date = market_time.date()

        quotes: list[RawOptionQuote] = []
        skipped = 0
        for group in groups:
            for side in ("calls", "puts"):
                for row in group.get(side) or []:
                    parsed = self._parse_quote(row)
                    if parsed is None:
                        skipped += 1
                        continue
                    quotes.append(parsed)
        if skipped:
            logger.warning("%s: skipped %d unparseable option rows", symbol, skipped)

        expirations = []
        for value in payload.get("expirationDates") or []:
            dt = _ts(value)
            if dt is not None:
                expirations.append(dt.date())

        return RawChain(
            symbol=symbol,
            name=quote.get("shortName") or quote.get("longName") or symbol,
            currency=quote.get("currency") or "USD",
            # One response carries the chain and the spot, so they cannot
            # disagree. Stated rather than left None so the check downstream
            # is exercised on this venue too instead of skipped.
            spot_currency=(quote.get("currency") or "USD").upper(),
            venue=Venue.OCC,
            spot=_num(quote.get("regularMarketPrice")),
            bid=_num(quote.get("bid")),
            ask=_num(quote.get("ask")),
            previous_close=_num(quote.get("regularMarketPreviousClose")),
            market_state=quote.get("marketState"),
            session_date=session_date,
            fetched_at=fetched_at,
            expirations=sorted(set(expirations)),
            quotes=quotes,
        )

    def _parse_quote(self, row: dict) -> RawOptionQuote | None:
        occ = row.get("contractSymbol")
        if not occ:
            return None
        try:
            ref = ContractRef.parse(occ)
        except OccParseError:
            return None

        # Cross-check the provider's own metadata against the identity encoded
        # in the symbol. Verified clean across a full 3,286-contract NVDA chain,
        # so a hit here is a genuine provider fault and the row is dropped
        # rather than written with metadata that contradicts its key.
        exp_dt = _ts(row.get("expiration"))
        strike = row.get("strike")
        if not ref.matches(
            float(strike) if strike is not None else None,
            exp_dt.date() if exp_dt else None,
        ):
            logger.warning(
                "Metadata contradicts OCC symbol for %s (provider strike=%r expiry=%r)",
                occ, strike, exp_dt,
            )
            return None

        iv = row.get("impliedVolatility")
        try:
            iv = float(iv) if iv is not None else None
        except (TypeError, ValueError):
            iv = None
        # Yahoo's sentinel for "my solver failed" is ~1e-05. Keeping it as a
        # number would make the comparison column meaningless.
        if iv is not None and (iv <= 1e-4 or iv > 10.0):
            iv = None

        oi = row.get("openInterest")
        return RawOptionQuote(
            ref=ref,
            bid=_num(row.get("bid")),
            ask=_num(row.get("ask")),
            last=_num(row.get("lastPrice")),
            last_trade_at=_ts(row.get("lastTradeDate")),
            volume=_int(row.get("volume")),
            open_interest=None if oi is None else _int(oi),
            iv_provider=iv,
            contract_size=_CONTRACT_SIZES.get(
                str(row.get("contractSize") or "REGULAR").upper(), 100
            ),
            currency=row.get("currency") or "USD",
        )

    async def validate(self, symbol: str, product: str | None = None) -> tuple[bool, str]:
        # `product` is accepted and ignored. An OCC series is named by the
        # underlying's own ticker, so there is no second code to carry.
        try:
            chain = await self.fetch_chain(symbol)
        except ChainUnavailable:
            return False, ""
        except Exception:
            logger.warning("Validation fetch failed for %s", symbol, exc_info=True)
            return False, ""
        if not chain.quotes:
            return False, ""
        return True, chain.name
