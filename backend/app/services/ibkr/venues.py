"""What IBKR calls each venue skewer can route to it.

IBKR is a source, not a venue: the same gateway serves Eurex today and could
serve Euronext. Everything that differs per venue on the IBKR side is here, as
data, so adding a venue is an entry and a config value rather than a branch.

What does *not* live here: the feed's delay and the venue's currency. Both are
``VenueSpec`` facts, read by the session gate before any provider exists, and a
second copy here could disagree with them.

Which entries are active is ``IBKR_VENUES`` (comma-separated venue values).
Only Eurex is verified: its product mapping was matched 1,048/1,048 on BMW and
1,262/1,262 on SAP. The Euronext entry is the expected shape, never run.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.config import settings
from app.domain.venue import Venue

# (symbol, venue_product) -> (IBKR option exchange, product code), or None
# when the underlying's market has no known IBKR exchange.
ProductMapper = Callable[[str, str | None], tuple[str, str] | None]
# product code -> (underlying ISIN, product currency), or None.
IsinSource = Callable[[str], Awaitable[tuple[str, str | None] | None]]


@dataclass(frozen=True, slots=True)
class IbkrVenue:
    venue: Venue
    product_for: ProductMapper
    # IBKR marks an adjusted series with a suffix on its trading class
    # (SAP.1 on Eurex); stripping this pattern gives the venue's product code.
    adjusted_class_suffix: re.Pattern[str]
    # Where an underlying's ISIN comes from, to resolve its IBKR conId. None
    # means only a hand-set ``underlyings.ibkr_con_id`` works on this venue.
    underlying_isin: IsinSource | None


def _eurex_product(symbol: str, venue_product: str | None) -> tuple[str, str]:
    from app.services.option_providers.eurex import EurexOptionProvider

    return "EUREX", EurexOptionProvider.product_for(symbol, venue_product)


async def _eurex_isin(product: str) -> tuple[str, str | None] | None:
    """Deutsche Boerse's ProductInfos names each option product's underlying ISIN."""
    from app.services.option_providers.eurex import EurexOptionProvider

    info = await EurexOptionProvider().product_info(product) or {}
    isin = info.get("UnderlyingISIN")
    return (isin, info.get("Currency")) if isin else None


# skewer's Euronext market code to IBKR's option exchange. Lisbon and Oslo are
# left out: their IBKR codes are unverified.
_EURONEXT_EXCHANGES = {"DAMS": "FTA", "DPAR": "MONEP", "DBRU": "BELFOX"}


def _euronext_product(symbol: str, venue_product: str | None) -> tuple[str, str] | None:
    from app.services.option_providers.euronext import EuronextOptionProvider

    market, code = EuronextOptionProvider.product_for(symbol, venue_product)
    exchange = _EURONEXT_EXCHANGES.get(market)
    return (exchange, code) if exchange else None


IBKR_VENUES: dict[Venue, IbkrVenue] = {
    Venue.EUREX: IbkrVenue(
        venue=Venue.EUREX,
        product_for=_eurex_product,
        adjusted_class_suffix=re.compile(r"\.\d+$"),
        underlying_isin=_eurex_isin,
    ),
    Venue.EURONEXT: IbkrVenue(
        venue=Venue.EURONEXT,
        product_for=_euronext_product,
        # Assumed to follow Eurex; unverified.
        adjusted_class_suffix=re.compile(r"\.\d+$"),
        # The Euronext EWS provider has never run, so there is no ISIN source.
        underlying_isin=None,
    ),
}


def enabled_venues() -> list[IbkrVenue]:
    """The venues ``IBKR_VENUES`` turns on, in that order."""
    names = [n.strip().lower() for n in settings.ibkr_venues.split(",") if n.strip()]
    return [IBKR_VENUES[Venue(n)] for n in names if Venue(n) in IBKR_VENUES]
