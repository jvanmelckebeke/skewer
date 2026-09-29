"""Option chains from Euronext, via the EWS (Euronext Web Service) API.

Written against "EURONEXT DERIVATIVES MARKETS WEB SERVICE API - CLIENT
SPECIFICATION" v1.4, 28 September 2022.

**This provider has never been run against the live API.** It requires an
``authKey`` that Euronext London issues per licence, and there is no anonymous
or sandbox mode in the specification. Everything below is implemented to the
document and unit-tested against response fixtures built from the document's
own worked examples. Treat the field mapping as plausible, not proven, until a
key exists and :func:`EuronextOptionProvider.validate` returns true against a
real contract.

The shape of the problem
------------------------

The whole specification exposes exactly two endpoints, ``instrumentDetail`` and
``intraday``, and both take **one instrument at a time**::

    https://gateway.euronext.com/api/instrumentDetail
        ?code=<CODE>-<C|P>-<YYYYMM>-<STRIKE>
        &exchCode=<DAMS|DBRU|DLIS|DOSL|DPAR>
        &codification=DER
        &sessionQuality=<RT|DT>
        &authKey=<KEY>

There is no chain endpoint, no instrument list, and no search. That is the
defining difference from the other two venues: Yahoo returns a whole chain in
one request and Eurex returns a whole product's contracts in one query, while
Euronext requires you to already know every (maturity, strike, right) triple
and then costs one HTTP round trip per contract.

So this provider *enumerates a grid* and treats a miss as evidence the contract
does not exist. That is genuinely expensive — a full chain is several hundred
requests — and it is why every bound here is configurable and why
``EURONEXT_MAX_REQUESTS`` is a hard stop rather than advice.

What it gives back is the richest of the three, though: a real order book
(``bsBidPx``/``bsAskPx``), open interest on both the current and previous
session, and an official settlement price. Unlike Eurex there is no need to
fall back to a settlement for the mark, because there is an actual two-sided
quote to take a mid from.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

import httpx

from app.config import settings
from app.domain import euronext as symbology
from app.domain.contracts import Right
from app.domain.venue import Venue, spec_for
from app.services.option_providers.base import (
    ChainUnavailable,
    OptionProvider,
    ProviderUnavailable,
    RawChain,
    RawOptionQuote,
    SpotSource,
    spot_from,
)

logger = logging.getLogger(__name__)

_SPEC = spec_for(Venue.EURONEXT)

# "RT" is real-time and "DT" is delayed, per section 3.1. Which one a key is
# entitled to is a licensing question, so it is configuration rather than a
# constant.
SESSION_QUALITY_REALTIME = "RT"
SESSION_QUALITY_DELAYED = "DT"

# The spec's own value for a derivatives request.
CODIFICATION = "DER"


def _num(value) -> float | None:
    """A feed number, or None.

    Every price in this API arrives as a *string* ("4188.5"), so the cast is
    not optional. Zero is preserved rather than mapped to None: unlike Yahoo,
    this feed omits a field it has no value for instead of publishing 0.0, so
    a zero here is a real zero and the zero-bid defence is not needed.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int:
    f = _num(value)
    return 0 if f is None else int(f)


def _dt(value) -> datetime | None:
    """Parse the feed's ``YYYYMMDD-HH:MM:SS`` timestamps.

    Naive in the response, and the venue is Central European, so they are
    localised to the venue's timezone rather than assumed UTC. Reading a
    17:39 Amsterdam settlement stamp as 17:39 UTC would put it two hours after
    the market closed.
    """
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y%m%d-%H:%M:%S", "%Y%m%d %H:%M:%S", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=_SPEC.expiry_tz)
    return None


def _date(value) -> date | None:
    """Parse the feed's ``YYYYMMDD`` dates (``instr.exchEndDt``)."""
    if not value:
        return None
    text = str(value).strip()[:8]
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _special_features(instr: dict) -> dict[str, str]:
    """``instr.specFeat[]`` flattened to ``{field: value}``.

    This is where the price denominators live, and they are the sharpest edge
    in the whole response — see :meth:`EuronextOptionProvider._scale`.
    """
    out: dict[str, str] = {}
    for item in instr.get("specFeat") or []:
        if isinstance(item, dict) and item.get("field") is not None:
            out[str(item["field"])] = item.get("value")  # type: ignore[assignment]
    return out


class EuronextAPIError(RuntimeError):
    """The gateway returned something that is not a usable instrument."""


class EuronextOptionProvider(OptionProvider):
    """Euronext option chains from the EWS derivatives API."""

    venue = Venue.EURONEXT
    #: The feed has a live order book, unlike Eurex.
    intraday = True

    def __init__(self, spot_source: SpotSource | None = None) -> None:
        self._url = settings.euronext_api_url
        self._auth_key = settings.euronext_auth_key
        self._session_quality = settings.euronext_session_quality
        self._timeout = settings.euronext_timeout_seconds
        self._concurrency = settings.euronext_concurrency
        self._max_requests = settings.euronext_max_requests
        self._strike_window = settings.euronext_strike_window_pct
        self._max_maturities = settings.euronext_max_maturities
        # Same composition as the Eurex provider: this API prices derivatives
        # and says nothing about the underlying share, so the spot comes from
        # the equity source.
        self._spot_source = spot_source

    @property
    def configured(self) -> bool:
        """Whether an auth key is present.

        Checked before every fetch so a missing key produces one clear log
        line rather than a storm of 401s, and so ``validate`` can reject an
        underlying at add time instead of accepting one that will never
        collect anything.
        """
        return bool(self._auth_key)

    # ---------------------------------------------------------------- HTTP

    async def _instrument(
        self, client: httpx.AsyncClient, identifier: str, exchange: str
    ) -> dict | None:
        """One ``instrumentDetail`` call. ``None`` when the contract is absent.

        A missing contract is the *expected* outcome for most of an enumerated
        grid, so it must be cheap and quiet. 404 and an empty ``instr`` body
        both mean "this strike is not listed", which is information, not an
        error.
        """
        params = {
            "code": identifier,
            "exchCode": exchange,
            "codification": CODIFICATION,
            "sessionQuality": self._session_quality,
            "authKey": self._auth_key,
        }
        try:
            response = await client.get(self._url, params=params)
        except httpx.HTTPError as exc:
            logger.debug("Euronext request failed for %s: %s", identifier, exc)
            return None

        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            # Not a per-contract problem. Raising stops the whole enumeration
            # instead of quietly returning an empty chain several hundred
            # requests later.
            raise EuronextAPIError(
                f"Euronext rejected the auth key (HTTP {response.status_code}). "
                f"Check EURONEXT_AUTH_KEY and the licence it belongs to."
            )
        if response.status_code >= 400:
            logger.debug("Euronext %s for %s", response.status_code, identifier)
            return None

        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict) or not payload.get("instr"):
            return None
        return payload

    # ------------------------------------------------------------- scaling

    @staticmethod
    def _scale(value: float | None, denominator: float) -> float | None:
        """Apply a price denominator.

        ``instr.specFeat[]`` publishes ``instrumentDenominator`` and
        ``strikePriceDenominator`` as integers (the spec's own example gives 10
        and 100). The specification never states in prose that prices must be
        divided by them, but a denominator that is not applied is not a
        denominator, and the sample data only makes sense that way.

        This is the single least certain thing in the provider, and it is
        wrong by a factor of ten or a hundred if the assumption is backwards.
        The cross-check in ``domain/euronext.check_strike`` is what will catch
        it: the identifier carries the true strike, so if scaling
        ``instr.strikePx`` disagrees with it, the mapping is wrong and ingest
        fails loudly instead of writing a chain that is off by 100x.
        """
        if value is None:
            return None
        if not denominator or denominator <= 0:
            return value
        return value / denominator

    # --------------------------------------------------------------- grid

    @staticmethod
    def product_for(symbol: str, product: str | None = None) -> tuple[str, str]:
        """``(exchange, contract_code)`` for an underlying.

        ``venue_product`` may carry the exchange explicitly as ``DBRU:ABI``,
        because the same code can list on more than one Euronext market. With
        no exchange given, Amsterdam is assumed — it is the deepest of the five
        and the most likely default, but it is an assumption and a wrong one
        yields an empty chain rather than an error.
        """
        raw = (product or symbol).strip().upper()
        if ":" in raw:
            exchange, _, code = raw.partition(":")
            return exchange.strip(), code.strip()
        return symbology.DEFAULT_EXCHANGE, raw.split(".")[0]

    def _maturities(self, on: date) -> list[tuple[int, int]]:
        """The next N monthly maturities, as ``(year, month)``.

        Monthly only. Euronext lists quarterlies and longer-dated series that
        are also monthly-aligned, so enumerating consecutive months finds them
        without needing the expiry calendar; weeklies, where they exist, are
        not addressable by a YYYYMM maturity at all and are therefore out of
        reach of this API shape.
        """
        out = []
        year, month = on.year, on.month
        for _ in range(self._max_maturities):
            out.append((year, month))
            month += 1
            if month > 12:
                year, month = year + 1, 1
        return out

    def _strikes(self, spot: float, step: float) -> list[float]:
        """Candidate strikes bracketing spot at ``step`` intervals."""
        if spot <= 0 or step <= 0:
            return []
        span = spot * self._strike_window
        lo = max(step, round((spot - span) / step) * step)
        hi = round((spot + span) / step) * step
        out, value = [], lo
        while value <= hi + 1e-9:
            out.append(round(value, 4))
            value += step
        return out

    @staticmethod
    def strike_step_for(spot: float) -> float:
        """A plausible strike interval for a given price level.

        Euronext publishes strike intervals per contract class and this API
        exposes no way to ask for them, so the ladder has to be guessed from
        the price level. These brackets match the common Amsterdam and Brussels
        equity-option ladders. A wrong guess costs wasted requests and a chain
        with holes, not incorrect data — every contract that comes back is
        real, whatever grid found it.
        """
        if spot < 10:
            return 0.5
        if spot < 50:
            return 1.0
        if spot < 100:
            return 2.0
        if spot < 250:
            return 5.0
        if spot < 500:
            return 10.0
        return 25.0

    # --------------------------------------------------------------- fetch

    async def fetch_chain(self, symbol: str, product: str | None = None) -> RawChain:
        if not self.configured:
            raise ChainUnavailable(
                "Euronext requires an auth key; set EURONEXT_AUTH_KEY. "
                "Euronext London issues one per licence."
            )

        exchange, code = self.product_for(symbol, product)
        spot_block = await spot_from(self._spot_source, symbol)
        spot = (spot_block or {}).get("spot")
        if not spot:
            raise ChainUnavailable(
                f"No spot for {symbol!r}; cannot enumerate a Euronext strike grid "
                f"without a price level to bracket."
            )

        today = datetime.now(tz=_SPEC.expiry_tz).date()
        step = self.strike_step_for(spot)
        candidates = [
            (maturity, strike, right)
            for maturity in self._maturities(today)
            for strike in self._strikes(spot, step)
            for right in (Right.CALL, Right.PUT)
        ]
        if len(candidates) > self._max_requests:
            logger.warning(
                "%s: grid of %d exceeds EURONEXT_MAX_REQUESTS=%d; truncating to the "
                "nearest maturities. Widen the cap or narrow the strike window.",
                symbol, len(candidates), self._max_requests,
            )
            candidates = candidates[: self._max_requests]

        quotes = await self._gather(candidates, code, exchange)
        if not quotes:
            raise ChainUnavailable(
                f"Euronext returned no listed contracts for {code!r} on {exchange!r}. "
                f"Check the contract code and exchange (venue_product accepts "
                f"'DBRU:ABI' form)."
            )

        return RawChain(
            symbol=symbol.upper(),
            name=(spot_block or {}).get("name") or symbol,
            currency=quotes[0].currency,
            # The spot comes from the equity source, not from Euronext, so its
            # currency is that source's to state. Copying the option's here
            # would make the mismatch guard compare a value with itself.
            spot_currency=(spot_block or {}).get("currency"),
            venue=Venue.EURONEXT,
            spot=spot,
            bid=(spot_block or {}).get("bid"),
            ask=(spot_block or {}).get("ask"),
            previous_close=(spot_block or {}).get("previous_close"),
            market_state=(spot_block or {}).get("market_state"),
            session_date=today,
            fetched_at=datetime.now(tz=timezone.utc),
            expirations=sorted({q.ref.expiration for q in quotes}),
            quotes=quotes,
        )

    async def _gather(
        self, candidates: list[tuple[tuple[int, int], float, Right]],
        code: str, exchange: str,
    ) -> list[RawOptionQuote]:
        """Fetch the grid, bounded in concurrency.

        The semaphore is not politeness, it is the difference between a poll
        and an incident: this is a licensed commercial gateway and a few
        hundred unthrottled requests per underlying per cycle is exactly the
        pattern that gets a key revoked.
        """
        semaphore = asyncio.Semaphore(self._concurrency)
        quotes: list[RawOptionQuote] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async def one(maturity, strike, right):
                identifier = symbology.instrument_code(code, right, maturity, strike)
                async with semaphore:
                    payload = await self._instrument(client, identifier, exchange)
                if payload is None:
                    return None
                return self._to_quote(payload, identifier, exchange)

            results = await asyncio.gather(
                *(one(m, s, r) for m, s, r in candidates), return_exceptions=True
            )

        auth_error: Exception | None = None
        for result in results:
            if isinstance(result, EuronextAPIError):
                auth_error = result
            elif isinstance(result, Exception):
                logger.debug("Euronext contract fetch failed: %s", result)
            elif result is not None:
                quotes.append(result)

        if auth_error is not None and not quotes:
            raise auth_error
        return quotes

    def _to_quote(
        self, payload: dict, identifier: str, exchange: str
    ) -> RawOptionQuote | None:
        instr = payload.get("instr") or {}
        if str(instr.get("type") or "").upper() not in ("OPT", ""):
            return None

        expiration = _date(instr.get("exchEndDt"))
        if expiration is None:
            # No expiry means no tenor, and no tenor means no pricing. The
            # contract is worthless to skewer even though the response parsed.
            logger.debug("%s: no exchEndDt in response", identifier)
            return None

        features = _special_features(instr)
        strike_denominator = _num(features.get("strikePriceDenominator")) or 1.0
        price_denominator = _num(features.get("instrumentDenominator")) or 1.0

        feed_strike = self._scale(_num(instr.get("strikePx")), strike_denominator)
        try:
            # The validation OCC gives for free and Eurex cannot give at all:
            # the identifier carries the strike, so a feed that disagrees with
            # its own key is a fault, and in particular this is what catches a
            # denominator applied the wrong way round.
            symbology.check_strike(identifier, feed_strike)
            ref = symbology.contract_ref(
                identifier=identifier,
                expiration=expiration,
                exchange=exchange,
                exercise_style=symbology.exercise_from_cfi(instr.get("cfiCode")),
                # The response carries no lot size. Euronext equity options
                # are 100 shares like everywhere else, so the venue default
                # applies; passing instrumentDenominator here would conflate a
                # price scale with a contract multiplier.
                contract_size=None,
                currency=instr.get("currency"),
            )
        except symbology.EuronextParseError:
            logger.warning(
                "Euronext metadata contradicts identifier %s (strikePx=%r denom=%r)",
                identifier, instr.get("strikePx"), strike_denominator,
            )
            return None

        book = instr.get("ordBook") or {}
        current = instr.get("currInstrSess") or {}
        previous = instr.get("prevInstrSess") or {}

        bid = self._scale(_num(book.get("bsBidPx")), price_denominator)
        ask = self._scale(_num(book.get("bsAskPx")), price_denominator)
        last = self._scale(_num(current.get("lastPx")), price_denominator)
        settlement = self._scale(_num(previous.get("setlmtPx")), price_denominator)

        # Open interest is published on both sessions. The current one is
        # authoritative when present; the previous session's is the fallback,
        # since OI is struck overnight and an early-morning poll may not have
        # today's yet.
        open_interest = _num(current.get("openInt"))
        if open_interest is None:
            open_interest = _num(previous.get("openInt"))

        return RawOptionQuote(
            ref=ref,
            bid=bid,
            ask=ask,
            last=last,
            last_trade_at=_dt(current.get("lastUpdate")),
            volume=_int(current.get("tradedQty")),
            open_interest=None if open_interest is None else int(open_interest),
            # The API publishes no implied volatility of its own, so there is
            # nothing to compare skewer's solve against on this venue.
            iv_provider=None,
            contract_size=ref.contract_size,
            currency=ref.currency,
            settlement=settlement,
        )

    async def validate(self, symbol: str, product: str | None = None) -> tuple[bool, str]:
        """Whether the contract code resolves to at least one live option.

        Deliberately cheap: it probes the nearest maturities at strikes around
        spot and stops at the first hit, rather than enumerating a full grid
        just to answer a yes/no question at add time.
        """
        if not self.configured:
            return False, ""

        exchange, code = self.product_for(symbol, product)
        spot_block = await spot_from(self._spot_source, symbol)
        spot = (spot_block or {}).get("spot")
        if not spot:
            return False, ""

        today = datetime.now(tz=_SPEC.expiry_tz).date()
        step = self.strike_step_for(spot)
        nearest = round(spot / step) * step
        probes = [
            (maturity, strike, Right.CALL)
            for maturity in self._maturities(today)[:3]
            for strike in (nearest, nearest + step, nearest - step)
        ]

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                for maturity, strike, right in probes:
                    identifier = symbology.instrument_code(code, right, maturity, strike)
                    payload = await self._instrument(client, identifier, exchange)
                    if payload is not None:
                        return True, (spot_block or {}).get("name") or symbol
        except EuronextAPIError as exc:
            raise ProviderUnavailable(f"Euronext rejected the auth key: {exc}") from exc
        except Exception as exc:
            raise ProviderUnavailable(f"Euronext did not answer for {code!r}: {exc}") from exc
        return False, ""


__all__ = ["EuronextOptionProvider", "EuronextAPIError"]
