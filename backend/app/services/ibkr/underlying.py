"""Find the IBKR conId of a Eurex underlying.

Eurex's ProductInfos names each option product's underlying by ISIN, and
ibkr-svc resolves an ISIN directly, so the lookup is exact rather than guessed
from the Yahoo ticker. It returns one row per listing, and a share can list in
several currencies (Stellantis is STLA on NYSE in USD and STLAP on Paris in
EUR), so candidates in the option product's currency are tried first.

Each candidate is still *verified*: it is accepted only if its Eurex option
catalog lists the product as a trading class. That check is what tells STLAP
from STLA, since ibkr-svc builds the chain from the underlying's symbol and
only STLAP finds FIA5. An uncached catalog costs ibkr-svc about 60 s, hence
the currency ordering. A name this cannot resolve can be set by hand in
``underlyings.ibkr_con_id``, which is never overwritten.
"""

from __future__ import annotations

import logging

from app.services.ibkr.base import IbkrGateway
from app.services.ibkr.matching import product_code

logger = logging.getLogger(__name__)


async def resolve_underlying(
    gateway: IbkrGateway, product: str, isin: str, currency: str | None
) -> int | None:
    """The conId whose Eurex option catalog lists ``product``, or None."""
    listings = await gateway.resolve_isin(isin)
    ordered = sorted(listings, key=lambda c: c.currency != (currency or c.currency))
    seen: set[int] = set()
    for listing in ordered:
        if listing.conId in seen:
            continue
        seen.add(listing.conId)
        chain = await gateway.option_contracts(listing.conId, "EUREX")
        if any(product_code(c.tradingClass) == product for c in chain):
            return listing.conId
    logger.warning(
        "No IBKR listing of %s has Eurex product %s in its option catalog (tried %s); "
        "set underlyings.ibkr_con_id by hand",
        isin, product, sorted(seen) or "none",
    )
    return None
