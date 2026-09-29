"""Daily IBKR catalog pass: give each Eurex contract its IBKR conId.

Runs off-hours. ibkr-svc caches each option catalog per Eurex day, so a second
run on the same day costs it nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.venue import Venue
from app.repositories.contract_repo import ContractRepository
from app.repositories.underlying_repo import UnderlyingRepository
from app.services.ibkr.client import IbkrClient
from app.services.ibkr.matching import match, product_code
from app.services.ibkr.underlying import resolve_underlying
from app.services.option_providers.eurex import EurexOptionProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LinkReport:
    symbol: str
    product: str
    linked: int
    written: int
    unmatched_rows: int
    ambiguous: int
    unmatched_ibkr: int
    error: str | None = None


async def link_eurex_contracts(
    db: AsyncSession, client: IbkrClient, today: date
) -> list[LinkReport]:
    """Resolve each active Eurex underlying's conId, then match its contracts."""
    underlyings = [u for u in await UnderlyingRepository(db).list_active() if u.venue == Venue.EUREX]
    # Captured while the rows are live: the loop commits.
    targets = [(u.id, u.symbol, u.venue_product, u.ibkr_con_id) for u in underlyings]
    contract_repo = ContractRepository(db)

    reports = []
    for uid, symbol, venue_product, con_id in targets:
        product = EurexOptionProvider.product_for(symbol, venue_product)
        try:
            if con_id is None:
                con_id = await resolve_underlying(client, symbol, product)
                if con_id is None:
                    reports.append(LinkReport(symbol, product, 0, 0, 0, 0, 0, "underlying not resolved"))
                    continue
                await UnderlyingRepository(db).set_ibkr_con_id(uid, con_id)
            contracts = [
                c for c in await client.option_contracts(con_id, "EUREX")
                if product_code(c.tradingClass) == product
            ]
            rows = await contract_repo.catalog_rows(uid, today)
            result = match(product, rows, contracts)
            written = await contract_repo.set_ibkr_con_ids(result.links)
        except Exception as exc:
            logger.warning("IBKR catalog pass failed for %s", symbol, exc_info=True)
            await db.rollback()
            reports.append(LinkReport(symbol, product, 0, 0, 0, 0, 0, str(exc)))
            continue
        reports.append(LinkReport(
            symbol, product, len(result.links), written,
            result.unmatched_rows, result.ambiguous, result.unmatched_ibkr,
        ))
    return reports
