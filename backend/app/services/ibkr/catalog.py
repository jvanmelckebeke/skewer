"""Daily IBKR catalog pass: give each contract on an IBKR venue its conId.

Runs off-hours, once per venue ``IBKR_VENUES`` enables. ibkr-svc caches each
option catalog per exchange day, so a second run on the same day costs it
nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.contract_repo import ContractRepository
from app.repositories.underlying_repo import UnderlyingRepository
from app.services.ibkr.base import IbkrGateway
from app.services.ibkr.matching import match, product_code
from app.services.ibkr.underlying import resolve_underlying
from app.services.ibkr.venues import IbkrVenue, enabled_venues

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


def _failed(symbol: str, product: str, error: str) -> LinkReport:
    return LinkReport(symbol, product, 0, 0, 0, 0, 0, error)


async def link_contracts(
    db: AsyncSession, gateway: IbkrGateway, today: date,
    venues: list[IbkrVenue] | None = None,
) -> list[LinkReport]:
    """Resolve each active underlying's conId on each venue, then match its contracts."""
    venues = enabled_venues() if venues is None else venues
    by_venue = {v.venue: v for v in venues}
    underlyings = [u for u in await UnderlyingRepository(db).list_active() if u.venue in by_venue]
    # Captured while the rows are live: the loop commits.
    targets = [(u.id, u.symbol, u.venue, u.venue_product, u.ibkr_con_id) for u in underlyings]
    contract_repo = ContractRepository(db)

    reports = []
    for uid, symbol, venue, venue_product, con_id in targets:
        spec = by_venue[venue]
        mapped = spec.product_for(symbol, venue_product)
        if mapped is None:
            reports.append(_failed(symbol, venue_product or symbol, "no IBKR exchange for this market"))
            continue
        exchange, product = mapped
        try:
            if con_id is None:
                found = await spec.underlying_isin(product) if spec.underlying_isin else None
                if found is None:
                    reports.append(_failed(symbol, product, "no underlying ISIN"))
                    continue
                isin, currency = found
                con_id = await resolve_underlying(
                    gateway, product, isin, currency, exchange, spec.adjusted_class_suffix
                )
                if con_id is None:
                    reports.append(_failed(symbol, product, "underlying not resolved"))
                    continue
                await UnderlyingRepository(db).set_ibkr_con_id(uid, con_id)
            contracts = [
                c for c in await gateway.option_contracts(con_id, exchange)
                if product_code(c.tradingClass, spec.adjusted_class_suffix) == product
            ]
            rows = await contract_repo.catalog_rows(uid, today)
            result = match(product, rows, contracts, spec.adjusted_class_suffix)
            written = await contract_repo.set_ibkr_con_ids(result.links)
        except Exception as exc:
            logger.warning("IBKR catalog pass failed for %s", symbol, exc_info=True)
            await db.rollback()
            reports.append(_failed(symbol, product, str(exc)))
            continue
        reports.append(LinkReport(
            symbol, product, len(result.links), written,
            result.unmatched_rows, result.ambiguous, result.unmatched_ibkr,
        ))
    return reports
