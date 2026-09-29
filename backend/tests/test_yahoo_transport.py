"""The Yahoo provider's transport: session reuse, crumb retry, circuit breaker.

No network. ``Ticker`` is replaced by a fake that records how often a session
was built and which symbols each request asked for.
"""

import pytest

from app.services.option_providers import yahoo as yahoo_mod
from app.services.option_providers.base import ChainUnavailable
from app.services.option_providers.yahoo import YahooOptionProvider, YahooThrottled
from app.services.option_providers.yahoo_rate_limit import YahooThrottle

CRUMB = "Invalid Crumb"


def _chain_payload(symbol: str) -> dict:
    return {
        "quote": {"regularMarketPrice": 100.0, "currency": "USD", "shortName": symbol},
        "expirationDates": [],
        "options": [{"calls": [], "puts": []}],
    }


class FakeTicker:
    """Stands in for ``yahooquery.Ticker``. ``responses`` is consumed per call."""

    built = 0
    responses: list = []
    requests: list[tuple[str, list[str]]] = []

    def __init__(self, symbols, **kwargs):
        FakeTicker.built += 1
        self.symbols = symbols

    def _next(self, kind):
        FakeTicker.requests.append((kind, list(self.symbols)))
        return FakeTicker.responses.pop(0)

    def _get_data(self, endpoint, params):
        return self._next("options")

    @property
    def quotes(self):
        return self._next("quotes")


@pytest.fixture
def fake_ticker(monkeypatch):
    FakeTicker.built = 0
    FakeTicker.responses = []
    FakeTicker.requests = []
    monkeypatch.setattr(yahoo_mod, "Ticker", FakeTicker)
    return FakeTicker


@pytest.fixture
def provider():
    return YahooOptionProvider(throttle=YahooThrottle(min_interval=0.0, backoff_schedule=(60.0,)))


async def test_one_session_serves_consecutive_requests(fake_ticker, provider):
    fake_ticker.responses = [{"NVDA": _chain_payload("NVDA")}, {"IBM": _chain_payload("IBM")}]

    await provider.fetch_chain("NVDA")
    await provider.fetch_chain("IBM")

    assert fake_ticker.built == 1
    assert fake_ticker.requests == [("options", ["NVDA"]), ("options", ["IBM"])]


async def test_session_is_rebuilt_after_its_ttl(fake_ticker):
    provider = YahooOptionProvider(throttle=YahooThrottle(min_interval=0.0), session_ttl=0.0)
    fake_ticker.responses = [{"NVDA": _chain_payload("NVDA")}, {"NVDA": _chain_payload("NVDA")}]

    await provider.fetch_chain("NVDA")
    await provider.fetch_chain("NVDA")

    assert fake_ticker.built == 2


async def test_crumb_rejection_retries_once_on_a_fresh_session(fake_ticker, provider):
    fake_ticker.responses = [{"NVDA": CRUMB}, {"NVDA": _chain_payload("NVDA")}]

    chain = await provider.fetch_chain("NVDA")

    assert chain.symbol == "NVDA"
    assert fake_ticker.built == 2
    assert not provider._throttle.is_blocked()


async def test_repeated_crumb_rejection_trips_the_breaker(fake_ticker, provider):
    fake_ticker.responses = [{"NVDA": CRUMB}, {"NVDA": CRUMB}]

    with pytest.raises(YahooThrottled):
        await provider.fetch_chain("NVDA")
    assert provider._throttle.is_blocked()

    # While the breaker is open nothing reaches Yahoo at all.
    with pytest.raises(YahooThrottled):
        await provider.fetch_chain("IBM")
    assert len(fake_ticker.requests) == 2


async def test_a_block_is_not_reported_as_no_options(fake_ticker, provider):
    """ChainUnavailable means the symbol has no options. A block is not that."""
    provider._throttle.record_invalid_crumb()

    with pytest.raises(YahooThrottled) as caught:
        await provider.fetch_chain("NVDA")
    assert not isinstance(caught.value, ChainUnavailable)


async def test_spot_comes_from_the_v7_quote_endpoint(fake_ticker, provider):
    fake_ticker.responses = [
        {"BMW.DE": {"regularMarketPrice": 55.72, "bid": 55.7, "ask": 55.74, "currency": "EUR"}}
    ]

    block = await provider.fetch_spot("BMW.DE")

    assert fake_ticker.requests == [("quotes", ["BMW.DE"])]
    assert block["regularMarketPrice"] == 55.72
    assert block["bid"] == 55.7


async def test_spot_during_a_block_is_none(fake_ticker, provider):
    provider._throttle.record_invalid_crumb()

    assert await provider.fetch_spot("BMW.DE") is None
    assert fake_ticker.requests == []
