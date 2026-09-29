"""What the three providers share: the spot helper, the chain loop, and the
difference between "no options here" and "the venue did not answer".
"""

from dataclasses import replace
from datetime import date

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.domain.contracts import Right
from app.domain.venue import Venue
from app.services import underlying_service
from app.services.chain_sync import _currency_mismatch
from app.services.option_providers.base import (
    ChainUnavailable,
    OptionProvider,
    ProviderUnavailable,
    spot_from,
)
from app.services.option_providers.eurex import EurexOptionProvider
from app.services.option_providers.euronext import EuronextOptionProvider
from app.services.option_providers.yahoo import YahooOptionProvider, YahooThrottled
from app.services.option_providers.yahoo_rate_limit import YahooThrottle
from tests.factories import make_chain, make_quote


class FakeSpot:
    """A SpotSource that answers with a fixed Yahoo quote block, or raises."""

    def __init__(self, block: dict | None = None, error: Exception | None = None):
        self._block = block
        self._error = error

    async def fetch_spot(self, symbol):
        if self._error:
            raise self._error
        return self._block

    async def fetch_close(self, symbol, on):
        return None


# ------------------------------------------------------------ spot helper


async def test_spot_carries_the_sources_own_currency():
    block = {"regularMarketPrice": 34.2, "currency": "eur", "bid": 0.0, "longName": "Philips"}

    spot = await spot_from(FakeSpot(block), "PHIA.AS")

    assert spot["spot"] == 34.2
    assert spot["currency"] == "EUR"
    assert spot["bid"] is None  # a zero bid is no bid
    assert spot["name"] == "Philips"


async def test_a_failing_spot_source_reads_as_no_spot():
    assert await spot_from(FakeSpot(error=RuntimeError("boom")), "BMW.DE") is None
    assert await spot_from(None, "BMW.DE") is None


# ------------------------------------------ Euronext currency guard


def _euronext_with_book(monkeypatch, spot_currency: str) -> EuronextOptionProvider:
    """A configured Euronext provider whose options settle in EUR."""
    provider = EuronextOptionProvider(
        spot_source=FakeSpot({"regularMarketPrice": 34.0, "currency": spot_currency})
    )
    provider._auth_key = "test"
    quote = replace(make_quote("PHIA", date(2026, 12, 18), Right.CALL, 34.0), currency="EUR")

    async def gather(candidates, code, exchange):
        return [quote]

    monkeypatch.setattr(provider, "_gather", gather)
    return provider


async def test_euronext_spot_currency_comes_from_the_spot_source(monkeypatch):
    chain = await _euronext_with_book(monkeypatch, "USD").fetch_chain("PHIA.AS")

    assert chain.currency == "EUR"
    assert chain.spot_currency == "USD"
    assert _currency_mismatch(chain)


async def test_euronext_matching_currencies_pass_the_guard(monkeypatch):
    chain = await _euronext_with_book(monkeypatch, "EUR").fetch_chain("PHIA.AS")

    assert not _currency_mismatch(chain)


# ---------------------------------------------------- default chain loop


class LoopProvider(OptionProvider):
    venue = Venue.EUREX

    async def fetch_chain(self, symbol, product=None):
        if symbol == "NONE":
            raise ChainUnavailable("no options")
        if symbol == "DOWN":
            raise ProviderUnavailable("venue down")
        return make_chain(symbol=symbol)

    async def validate(self, symbol, product=None):
        return True, symbol


async def test_default_fetch_chains_skips_failures_and_keeps_the_rest():
    chains = await LoopProvider().fetch_chains(["BMW", "NONE", "DOWN", "SAP"])

    assert sorted(chains) == ["BMW", "SAP"]


# ------------------------------------------------ unavailable, per venue


async def test_yahoo_validate_raises_while_the_breaker_is_open():
    provider = YahooOptionProvider(throttle=YahooThrottle(min_interval=0.0))
    provider._throttle.record_invalid_crumb()

    with pytest.raises(ProviderUnavailable):
        await provider.validate("NVDA")
    assert issubclass(YahooThrottled, ProviderUnavailable)


async def test_eurex_validate_raises_when_the_api_fails(monkeypatch):
    provider = EurexOptionProvider()

    async def broken(code):
        raise RuntimeError("503 from Deutsche Boerse")

    monkeypatch.setattr(provider, "product_info", broken)
    with pytest.raises(ProviderUnavailable):
        await provider.validate("BMW.DE")


async def test_eurex_validate_still_says_no_for_an_unknown_product(monkeypatch):
    provider = EurexOptionProvider()

    async def missing(code):
        return None

    monkeypatch.setattr(provider, "product_info", missing)
    assert await provider.validate("NOPE.DE") == (False, "")


# ------------------------------------------------------ the add endpoint


class Verdict:
    """A provider whose validate() gives a fixed answer or raises."""

    def __init__(self, answer=(False, ""), error: Exception | None = None):
        self._answer = answer
        self._error = error

    async def validate(self, symbol, product=None):
        if self._error:
            raise self._error
        return self._answer


@pytest_asyncio.fixture
async def default_group(db):
    from app.models import Group

    group = Group(name="Watchlist", is_default=True, position=0)
    db.add(group)
    await db.commit()
    return group


def _route(monkeypatch, by_venue: dict):
    monkeypatch.setattr(underlying_service, "for_venue", lambda venue: by_venue[venue])


async def test_an_unreachable_venue_is_try_later_not_no_options(db, default_group, monkeypatch):
    _route(monkeypatch, {Venue.EUREX: Verdict(error=ProviderUnavailable("timeout"))})

    with pytest.raises(HTTPException) as caught:
        await underlying_service.add_underlying(db, "BMW.DE", venue=Venue.EUREX)

    assert caught.value.status_code == 503
    assert "Try again later" in caught.value.detail


async def test_detection_cannot_conclude_no_options_past_a_venue_that_was_down(
    db, default_group, monkeypatch
):
    _route(monkeypatch, {
        Venue.OCC: Verdict(error=YahooThrottled("blocked")),
        Venue.EUREX: Verdict((False, "")),
    })

    with pytest.raises(HTTPException) as caught:
        await underlying_service.add_underlying(db, "NVDA")

    assert caught.value.status_code == 503


async def test_a_venue_that_answers_still_wins_when_another_is_down(db, default_group, monkeypatch):
    _route(monkeypatch, {
        Venue.OCC: Verdict(error=YahooThrottled("blocked")),
        Venue.EUREX: Verdict((True, "BMW")),
    })

    row = await underlying_service.add_underlying(db, "BMW.DE")

    assert row.venue == Venue.EUREX


async def test_a_venue_that_answers_no_is_still_a_422(db, default_group, monkeypatch):
    _route(monkeypatch, {Venue.OCC: Verdict((False, "")), Venue.EUREX: Verdict((False, ""))})

    with pytest.raises(HTTPException) as caught:
        await underlying_service.add_underlying(db, "NOPE")

    assert caught.value.status_code == 422
