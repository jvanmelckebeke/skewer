"""Find the IBKR conId of a Eurex underlying.

skewer knows an underlying by its Yahoo ticker and its Eurex product code.
IBKR knows it by conId, and the mapping between the two is not mechanical, so
this guesses candidates from the ticker and then *verifies* each one: a
candidate is accepted only if IBKR's Eurex option catalog for it contains the
product as a trading class. A wrong guess therefore resolves nothing rather
than the wrong company. A name this cannot resolve can be set by hand in
``underlyings.ibkr_con_id``, which is never overwritten.
"""

from __future__ import annotations

import logging

from app.services.ibkr.client import ContractInfo, IbkrClient
from app.services.ibkr.matching import product_code

logger = logging.getLogger(__name__)

# Yahoo's exchange suffix to IBKR's primary exchange for the share.
_PRIMARY_EXCHANGE = {
    "DE": "IBIS",
    "AS": "AEB",
    "PA": "SBF",
    "BR": "ENEXT.BE",
    "MI": "BVME",
}

# Indices have no suffix to read; IBKR lists them as IND on the futures venue.
_INDICES = {"^STOXX50E": ("ESTX50", "EUREX")}


async def _candidates(client: IbkrClient, symbol: str) -> list[ContractInfo]:
    if symbol in _INDICES:
        ib_symbol, exchange = _INDICES[symbol]
        return await client.resolve(secType="IND", symbol=ib_symbol, exchange=exchange)
    root, _, suffix = symbol.rpartition(".")
    primary = _PRIMARY_EXCHANGE.get(suffix)
    if not root or primary is None:
        return []
    return await client.resolve(
        secType="STK", symbol=root, exchange="SMART", primaryExchange=primary
    )


async def resolve_underlying(client: IbkrClient, symbol: str, product: str) -> int | None:
    """The conId whose Eurex option catalog lists ``product``, or None."""
    for candidate in await _candidates(client, symbol):
        chain = await client.option_contracts(candidate.conId, "EUREX")
        if any(product_code(c.tradingClass) == product for c in chain):
            return candidate.conId
    logger.warning(
        "%s: no IBKR underlying lists Eurex product %s; set underlyings.ibkr_con_id by hand",
        symbol, product,
    )
    return None
