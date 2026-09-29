"""Find the IBKR conId of an underlying from its ISIN.

The ISIN comes from the venue (Eurex's ProductInfos names each option
product's underlying), and ibkr-svc resolves an ISIN directly, so the lookup
is exact rather than guessed from the Yahoo ticker. It returns one row per listing, and a share can list in
several currencies (Stellantis is STLA on NYSE in USD and STLAP on Paris in
EUR), so candidates in the option product's currency are tried first.

Each candidate is still *verified*: it is accepted only if its option catalog
on the venue's IBKR exchange lists the product as a trading class. That check is what tells STLAP
from STLA, since ibkr-svc builds the chain from the underlying's symbol and
only STLAP finds FIA5. An uncached catalog costs ibkr-svc about 60 s, hence
the currency ordering. A name this cannot resolve can be set by hand in
``underlyings.ibkr_con_id``, which is never overwritten.
"""

from __future__ import annotations

import logging
import re

from app.services.ibkr.base import IbkrGateway
from app.services.ibkr.matching import product_code

logger = logging.getLogger(__name__)


async def resolve_underlying(
    gateway: IbkrGateway, product: str, isin: str, currency: str | None,
    exchange: str, adjusted_suffix: re.Pattern[str],
) -> int | None:
    """The conId whose option catalog on ``exchange`` lists ``product``, or None."""
    listings = await gateway.resolve_isin(isin)
    ordered = sorted(listings, key=lambda c: c.currency != (currency or c.currency))
    seen: set[int] = set()
    for listing in ordered:
        if listing.conId in seen:
            continue
        seen.add(listing.conId)
        chain = await gateway.option_contracts(listing.conId, exchange)
        if any(product_code(c.tradingClass, adjusted_suffix) == product for c in chain):
            return listing.conId
    logger.warning(
        "No IBKR listing of %s has %s product %s in its option catalog (tried %s); "
        "set underlyings.ibkr_con_id by hand",
        isin, exchange, product, sorted(seen) or "none",
    )
    return None
