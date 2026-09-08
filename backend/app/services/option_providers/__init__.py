"""Option provider registry — resolves a *venue* to a provider singleton.

This used to resolve the single ``OPTION_PROVIDER`` setting to one global
instance, which was right while every underlying skewer tracked was US-listed.
It stopped being right when Eurex arrived: NVDA and BMW.DE are polled in the
same collection cycle and need different feeds, so "which provider" is a
property of the underlying being polled, not of the process.

Routing is therefore by ``Venue``, stored on ``underlyings.venue``. Adding a
third venue means an entry in ``_PROVIDERS`` and nothing else — no branch in
the sync loop, no change to the jobs.

The providers are singletons because both hold connection-pool-shaped state
(``httpx`` clients, yahooquery sessions) that is wasteful to rebuild per poll.
"""

from app.config import settings
from app.domain.venue import Venue
from app.services.option_providers.base import (
    ChainUnavailable,
    OptionProvider,
    RawChain,
    RawOptionQuote,
)
from app.services.option_providers.eurex import EurexOptionProvider
from app.services.option_providers.yahoo import YahooOptionProvider

__all__ = [
    "ChainUnavailable", "OptionProvider", "RawChain", "RawOptionQuote",
    "init_option_provider", "get_option_provider", "for_venue", "all_providers",
]

_PROVIDERS: dict[Venue, OptionProvider] = {}

# Name-based aliases, kept so ``OPTION_PROVIDER=yahoo`` in an existing .env
# still means something and so a config typo still fails loudly.
_BY_NAME: dict[str, Venue] = {
    "yahoo": Venue.OCC,
    "occ": Venue.OCC,
    "eurex": Venue.EUREX,
}


def init_option_provider() -> None:
    """Instantiate every provider (called once at startup).

    All of them, not just the configured one: the point of venue routing is
    that they coexist. Instantiation is cheap and does no I/O.
    """
    global _PROVIDERS

    name = settings.option_provider
    if name not in _BY_NAME:
        raise ValueError(
            f"Unknown option provider: {name!r}. Available: {sorted(_BY_NAME)}"
        )

    yahoo = YahooOptionProvider()
    _PROVIDERS = {
        Venue.OCC: yahoo,
        # Eurex publishes no underlying price, so it borrows Yahoo's equity
        # quote for spot. The dependency is passed in rather than imported
        # inside the provider so it is visible here, where the wiring lives.
        Venue.EUREX: EurexOptionProvider(spot_source=yahoo),
    }


def for_venue(venue: Venue | str) -> OptionProvider:
    """The provider that speaks for ``venue``."""
    if not _PROVIDERS:
        raise RuntimeError(
            "Option providers not initialized — call init_option_provider() first"
        )
    key = venue if isinstance(venue, Venue) else Venue(venue)
    provider = _PROVIDERS.get(key)
    if provider is None:
        raise ValueError(f"No option provider registered for venue {key!r}")
    return provider


def all_providers() -> dict[Venue, OptionProvider]:
    """Every registered provider, keyed by venue."""
    return dict(_PROVIDERS)


def get_option_provider() -> OptionProvider:
    """The default provider, for the paths that are not venue-aware yet.

    Retained so existing call sites keep working. Anything that knows which
    underlying it is dealing with should call :func:`for_venue` instead — this
    one silently assumes US listings.
    """
    return for_venue(_BY_NAME[settings.option_provider])
