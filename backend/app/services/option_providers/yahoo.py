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
from datetime import datetime, timezone

from yahooquery import Ticker

from app.domain.occ import ContractRef, OccParseError
from app.services.option_providers.base import (
    ChainUnavailable,
    OptionProvider,
    RawChain,
    RawOptionQuote,
)

logger = logging.getLogger(__name__)

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


class YahooOptionProvider(OptionProvider):
    """Option chains via Yahoo Finance."""

    async def fetch_chain(self, symbol: str) -> RawChain:
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
        ticker = Ticker(symbols, asynchronous=False, validate=False)
        data = ticker._get_data("options", {"getAllData": True})
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
            contract_size=row.get("contractSize") or "REGULAR",
            currency=row.get("currency") or "USD",
        )

    async def validate(self, symbol: str) -> tuple[bool, str]:
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
