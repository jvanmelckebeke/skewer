"""Option chains from Eurex, via Deutsche Boerse's public GraphQL API.

What this feed is, and what it is not
-------------------------------------

Deutsche Boerse publishes a free, anonymous GraphQL endpoint carrying Eurex
reference data. Despite being documented as metadata, it serves three things
skewer wants:

* ``ProductInfos`` — the option product (currency, multiplier, underlying ISIN)
* ``Contracts`` — every listed contract's terms, including ``ExerciseStyle``,
  ``VersionNr`` and ``PreviousDaySettlementPrice``
* ``SettlementPrices`` — one official settlement price per contract per session

What it does **not** serve is an order book. There is no bid, no ask, no
intraday anything. That is the defining constraint of this provider and it
shapes everything below: a Eurex poll produces one row per contract per
session, not a cross-section on a five-minute grid. ``intraday = False`` is how
that fact reaches the rest of the system.

The settlement price is nonetheless a real price. It is struck by the exchange
against the closing auction and is the most authoritative number of the day —
the opposite of Yahoo's zero bid, which is the *absence* of a quote. So it is
carried in ``RawOptionQuote.settlement`` and is allowed to become a mark, while
``bid``/``ask`` stay NULL because there genuinely was no book in the data.

Spot comes from somewhere else
------------------------------

Eurex publishes no underlying price, and an option surface with no spot has no
moneyness axis and therefore no implied volatility. Yahoo does serve European
*equity* quotes even though it serves no European options, so this provider
composes the two: Eurex for the surface, Yahoo for the spot it is measured
against. That is a real coupling and it is deliberate — the alternative is a
chain that can never be priced.

Identity
--------

Contracts are keyed by ``EUX:`` + ISIN. See ``domain/eurex.py`` for why, and
for the honest note about losing the self-validating property an OCC symbol
has.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from app.config import settings
from app.domain import eurex as eurex_symbology
from app.domain.venue import Venue, spec_for
from app.services.option_providers.base import (
    ChainUnavailable,
    OptionProvider,
    RawChain,
    RawOptionQuote,
)

logger = logging.getLogger(__name__)

_SPEC = spec_for(Venue.EUREX)

# One page is the API's maximum. BMW lists 952 contracts, so most single-stock
# products fit in one round trip; index products do not, hence the cursor loop.
PAGE_SIZE = 1000

# How far back to look for the most recent settlement session. A long weekend
# plus a German public holiday can put four non-trading days in a row, and the
# feed publishes overnight, so "yesterday" is not a safe assumption. Seven days
# covers every gap in the Eurex calendar without pulling a meaningful amount of
# extra data, since the query is then narrowed to the single latest date found.
SETTLEMENT_LOOKBACK_DAYS = 7

# Guard against a runaway cursor loop if the API ever reports hasNextPage
# without advancing. Twenty pages is 20k contracts, far beyond any Eurex
# single-stock product.
MAX_PAGES = 20

_CONTRACT_FIELDS = """
    ContractID
    ISIN
    Product
    CallPut
    Strike
    ExerciseStyle
    SettlementType
    ContractSize
    VersionNr
    ExpirationDate
    LastTradingDate
    PreviousDaySettlementPrice
"""

_CONTRACTS_QUERY = """
query Contracts($product: String!, $first: Int!, $after: String) {
  Contracts(
    filter: { Product: { eq: $product } }
    page: { first: $first, after: $after }
  ) {
    pageInfo { hasNextPage endCursor }
    data { %s }
  }
}
""" % _CONTRACT_FIELDS

_PRODUCT_QUERY = """
query Product($product: String!) {
  ProductInfos(filter: { Product: { eq: $product } }, page: { first: 5 }) {
    data {
      Product
      Name
      ProductLine
      Currency
      ContractSize
      UnderlyingISIN
      UnderlyingName
    }
  }
}
"""

# Deliberately unpaginated, and the variable is Date! rather than String!.
# Both are forced by the API: a range filter (`ge`) cannot be combined with
# `page` at all ("The provided filter cannot be combined with pagination"), and
# a String there is a 400. Unpaginated is fine here because the window is small
# — a week of BMW settlements is ~5k rows in one response — and it is the only
# way to ask "which session is the newest you have?" in a single round trip.
_SETTLEMENT_QUERY = """
query Settlements($product: String!, $since: Date!) {
  SettlementPrices(
    filter: { Product: { eq: $product }, SettlementDate: { ge: $since } }
  ) {
    data { SettlementDate ContractID SettlementPrice PriceType ContractType }
  }
}
"""


def _num(value) -> float | None:
    """A feed number, or None.

    Unlike Yahoo's ``_num`` this does **not** map zero to None. A Eurex
    settlement price of 0.01 is a real price for a far out-of-the-money option,
    and the feed has no zero-means-absent convention to defend against: a
    contract with no settlement simply has no row. Mapping 0 to None here
    would silently discard the cheapest end of every chain.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f >= 0.0 else None


def _date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class EurexGraphQLError(RuntimeError):
    """The API returned a GraphQL-level error rather than data."""


class EurexOptionProvider(OptionProvider):
    """Eurex option chains from the Deutsche Boerse reference-data API."""

    venue = Venue.EUREX
    #: No order book in the feed. One settled row per contract per session.
    intraday = False

    def __init__(self, spot_source: OptionProvider | None = None) -> None:
        self._url = settings.eurex_graphql_url
        self._api_key = settings.eurex_api_key
        self._timeout = settings.eurex_timeout_seconds
        # Injected rather than imported so tests can supply a stub and so the
        # dependency on Yahoo is visible in the constructor instead of buried
        # in a method.
        self._spot_source = spot_source

    # ---------------------------------------------------------------- HTTP

    async def _query(self, query: str, variables: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-DBP-APIKEY"] = self._api_key
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                self._url, json={"query": query, "variables": variables}, headers=headers
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            # Surface the first message: the API's validation errors are
            # specific and actionable ("Field X is not defined by type Y"),
            # and swallowing them turns a typo into an empty chain.
            raise EurexGraphQLError(payload["errors"][0].get("message", "unknown error"))
        return payload.get("data") or {}

    async def _paged(self, query: str, variables: dict, root: str) -> list[dict]:
        """Follow the cursor until the API says there is no next page."""
        rows: list[dict] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            data = await self._query(query, {**variables, "first": PAGE_SIZE, "after": cursor})
            block = data.get(root) or {}
            rows.extend(block.get("data") or [])
            info = block.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return rows
            next_cursor = info.get("endCursor")
            if not next_cursor or next_cursor == cursor:
                logger.warning("%s: cursor did not advance; stopping pagination", root)
                return rows
            cursor = next_cursor
        logger.warning("%s: hit MAX_PAGES (%d); chain may be truncated", root, MAX_PAGES)
        return rows

    # ------------------------------------------------------------- mapping

    @staticmethod
    def product_for(symbol: str, product: str | None = None) -> str:
        """The Eurex product code for an underlying.

        Explicit ``product`` wins. Otherwise the ticker's exchange suffix is
        stripped, which turns BMW.DE into BMW and is right often enough to be
        a useful default — but only a default. Euronext's Ahold is "AH" against
        a Yahoo ticker of "AD.AS", so the mapping is genuinely not derivable in
        general and ``underlyings.venue_product`` exists to record it.
        """
        if product:
            return product.strip().upper()
        return symbol.split(".")[0].strip().upper()

    # --------------------------------------------------------------- fetch

    async def fetch_chain(self, symbol: str, product: str | None = None) -> RawChain:
        code = self.product_for(symbol, product)

        info = await self._product_info(code)
        if info is None:
            raise ChainUnavailable(f"No Eurex option product for {symbol!r} (tried {code!r})")

        contracts = await self._paged(_CONTRACTS_QUERY, {"product": code}, "Contracts")
        if not contracts:
            raise ChainUnavailable(f"Eurex product {code!r} listed no contracts")

        settlements, session_date = await self._latest_settlements(code)

        currency = info.get("Currency") or _SPEC.currency
        default_size = int(info.get("ContractSize") or _SPEC.default_contract_size)

        quotes: list[RawOptionQuote] = []
        expirations: set[date] = set()
        skipped = 0
        for row in contracts:
            quote = self._to_quote(row, settlements, currency, default_size)
            if quote is None:
                skipped += 1
                continue
            quotes.append(quote)
            expirations.add(quote.ref.expiration)
        if skipped:
            logger.warning("%s: skipped %d unusable Eurex contract rows", code, skipped)

        spot_block = await self._spot(symbol)
        fetched_at = datetime.now(tz=timezone.utc)

        # The spot must come from the same session as the settlement prices.
        # This is the single most important line in the provider. Eurex
        # settlements are struck at yesterday's close; pricing them against a
        # live spot mixes two moments and pushed 252 of BMW's 952 contracts
        # below their own no-arbitrage bound in testing, while silently
        # skewing every contract that did solve. The live quote is kept only
        # as a fallback for the case where no historical close is available at
        # all, since a wrong-session spot still beats no pricing basis.
        spot = None
        if session_date is not None:
            spot = await self._session_close(symbol, session_date)
        if spot is None:
            spot = (spot_block or {}).get("spot")
            if spot is not None:
                logger.warning(
                    "%s: no close for session %s; pricing against live spot instead",
                    symbol, session_date,
                )

        return RawChain(
            symbol=symbol.upper(),
            name=info.get("UnderlyingName") or info.get("Name") or symbol,
            currency=currency,
            venue=Venue.EUREX,
            spot=spot,
            # No book in the feed, so the underlying's own bid/ask is all
            # there is, and Yahoo only reliably supplies it during the
            # session. NULL is the honest value the rest of the time.
            bid=(spot_block or {}).get("bid"),
            ask=(spot_block or {}).get("ask"),
            previous_close=(spot_block or {}).get("previous_close"),
            market_state=(spot_block or {}).get("market_state"),
            session_date=session_date,
            fetched_at=fetched_at,
            expirations=sorted(expirations),
            quotes=quotes,
        )

    async def fetch_chains(self, symbols: list[str]) -> dict[str, RawChain]:
        """Fetch several chains, isolating failures.

        Sequential rather than gathered: this is a free, shared, rate-limited
        API key, and hammering it in parallel is how a public endpoint stops
        being public. A handful of European names per cycle is not a latency
        problem worth that risk.
        """
        out: dict[str, RawChain] = {}
        for symbol in symbols:
            try:
                chain = await self.fetch_chain(symbol)
            except ChainUnavailable:
                continue
            except Exception:
                logger.warning("Eurex chain fetch failed for %s", symbol, exc_info=True)
                continue
            out[chain.symbol] = chain
        return out

    async def validate(self, symbol: str, product: str | None = None) -> tuple[bool, str]:
        code = self.product_for(symbol, product)
        try:
            info = await self._product_info(code)
        except Exception:
            logger.warning("Eurex validation failed for %s", symbol, exc_info=True)
            return False, ""
        if info is None:
            return False, ""
        return True, info.get("UnderlyingName") or info.get("Name") or symbol

    # ------------------------------------------------------------ internals

    async def _product_info(self, code: str) -> dict | None:
        data = await self._query(_PRODUCT_QUERY, {"product": code})
        rows = (data.get("ProductInfos") or {}).get("data") or []
        # A product code can carry both a future and an option line (BMW is
        # both). Only the option line is a chain.
        for row in rows:
            if str(row.get("ProductLine") or "").upper() == "OPTION":
                return row
        return None

    async def _latest_settlements(self, code: str) -> tuple[dict[int, float], date | None]:
        """Settlement prices for the most recent session the feed has.

        Two steps rather than one because the API has no "latest" filter: pull
        a week, find the newest date present, then keep only that date. Taking
        the newest date *in the data* rather than computing it from a calendar
        is what makes this correct across holidays without skewer needing to
        know the Eurex trading calendar.
        """
        since = (datetime.now(tz=timezone.utc).date() - timedelta(days=SETTLEMENT_LOOKBACK_DAYS))
        data = await self._query(
            _SETTLEMENT_QUERY, {"product": code, "since": since.isoformat()}
        )
        rows = (data.get("SettlementPrices") or {}).get("data") or []
        if not rows:
            return {}, None

        dates = [d for d in (_date(r.get("SettlementDate")) for r in rows) if d is not None]
        if not dates:
            return {}, None
        session_date = max(dates)

        out: dict[int, float] = {}
        for row in rows:
            if _date(row.get("SettlementDate")) != session_date:
                continue
            cid = row.get("ContractID")
            price = _num(row.get("SettlementPrice"))
            if cid is None or price is None:
                continue
            out[int(cid)] = price
        return out, session_date

    def _to_quote(
        self, row: dict, settlements: dict[int, float], currency: str, default_size: int
    ) -> RawOptionQuote | None:
        isin = row.get("ISIN")
        expiration = _date(row.get("ExpirationDate"))
        strike = row.get("Strike")
        if not isin or expiration is None or strike is None:
            return None

        try:
            ref = eurex_symbology.contract_ref(
                isin=isin,
                product=row.get("Product") or "",
                expiration=expiration,
                right=eurex_symbology.right_from_callput(row.get("CallPut")),
                strike=float(strike),
                exercise_style=eurex_symbology.exercise_from_feed(row.get("ExerciseStyle")),
                contract_size=int(row.get("ContractSize") or default_size),
                currency=currency,
            )
        except (eurex_symbology.EurexParseError, TypeError, ValueError):
            return None

        cid = row.get("ContractID")
        settlement = settlements.get(int(cid)) if cid is not None else None
        # PreviousDaySettlementPrice rides along on the contract row and is a
        # useful fallback when the settlement query has not caught up, but it
        # is explicitly the *previous* day, so it is only used when today's is
        # missing and it is never presented as today's mark.
        if settlement is None:
            settlement = _num(row.get("PreviousDaySettlementPrice"))

        return RawOptionQuote(
            ref=ref,
            # There is no order book in this feed. Not "the book was empty" —
            # the field does not exist. NULL is the only truthful value.
            bid=None,
            ask=None,
            last=None,
            last_trade_at=None,
            volume=0,
            # Open interest is a fee-based file on Eurex, not part of the free
            # reference API. NULL rather than 0, which would read as "no open
            # interest" instead of "not published to us".
            open_interest=None,
            iv_provider=None,
            contract_size=ref.contract_size,
            currency=ref.currency,
            settlement=settlement,
        )

    async def _session_close(self, symbol: str, on: date) -> float | None:
        """The underlying's close for the settlement session."""
        if self._spot_source is None:
            return None
        getter = getattr(self._spot_source, "fetch_close", None)
        if getter is None:
            return None
        try:
            return await getter(symbol, on)
        except Exception:
            logger.warning("Session close lookup failed for %s on %s", symbol, on, exc_info=True)
            return None

    async def _spot(self, symbol: str) -> dict | None:
        """The underlying's spot, from the injected equity source."""
        if self._spot_source is None:
            return None
        getter = getattr(self._spot_source, "fetch_spot", None)
        if getter is None:
            return None
        block = await getter(symbol)
        if not isinstance(block, dict):
            return None

        def _pos(value):
            try:
                f = float(value)
            except (TypeError, ValueError):
                return None
            return f if f > 0 else None

        return {
            "spot": _pos(block.get("regularMarketPrice")),
            "bid": _pos(block.get("bid")),
            "ask": _pos(block.get("ask")),
            "previous_close": _pos(block.get("regularMarketPreviousClose")),
            "market_state": block.get("marketState"),
        }


__all__ = ["EurexOptionProvider", "EurexGraphQLError"]
