"""Skewer's background jobs.

Job bodies own their DB sessions and swallow their own exceptions — a failing
run logs and waits for the next trigger, never crashes the scheduler.

Three jobs, and the split between the first two is the whole capture design:

``chain_poll``      fires on the base grid and persists the tenor buckets whose
                    slot it is. Gated per venue on its market actually being open.
``eod_snapshot``    fires after the close and persists *everything* once,
                    including LEAPS and open interest.
``contract_catalog``keeps the contract dimension table fresh over the weekend so
                    newly listed expirations are known before Monday's open.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.background_tasks.registry import background_task
from app.config import settings
from app.database import async_session
from app.services.chain_sync import sync_all
from app.services.ibkr import get_gateway
from app.services.ibkr.catalog import link_contracts

logger = logging.getLogger(__name__)


def _summarise(results) -> str:
    fetched = sum(r.fetched for r in results)
    persisted = sum(r.persisted for r in results)
    new = sum(r.new_contracts for r in results)
    solved = sum(r.iv_solved for r in results)
    skipped = [r.symbol for r in results if r.skipped_reason]
    msg = (
        f"{len(results)} underlyings, {fetched} contracts fetched, "
        f"{persisted} quotes stored, {solved} IVs solved, {new} new contracts"
    )
    if skipped:
        msg += f"; skipped {', '.join(skipped)}"
    return msg


@background_task("chain_poll", trigger=IntervalTrigger(minutes=settings.base_poll_minutes))
async def poll_chains() -> None:
    """Intraday chain poll on the base grid.

    The market-open gate is what makes the adaptive-cadence arithmetic hold:
    without it an underlying would be polled 288 times a day instead of 78,
    and every off-hours run would store a frozen chain whose bid/ask are all
    zero — rows that carry no information but do carry storage. The gate is
    per venue (``only_due``), so the job itself fires on every tick.
    """
    async with async_session() as db:
        try:
            results = await sync_all(db, full=False, only_due=True)
        except Exception:
            logger.exception("Chain poll failed")
            return
    if results:
        logger.info("Chain poll: %s", _summarise(results))


def _eod_trigger() -> CronTrigger | None:
    """EOD snapshot trigger from ``EOD_SNAPSHOT_CRON``.

    A malformed value disables only this job, loudly — the intraday poll and
    the catalog refresh keep running.
    """
    parts = settings.eod_snapshot_cron.split()
    if len(parts) != 5:
        logger.warning(
            "Malformed EOD_SNAPSHOT_CRON %r (expected 5 fields) — the eod_snapshot job "
            "is disabled; all other background jobs run normally",
            settings.eod_snapshot_cron,
        )
        return None
    return CronTrigger(
        minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4],
        timezone=timezone.utc,
    )


@background_task("eod_snapshot", trigger=_eod_trigger)
async def eod_snapshot() -> None:
    """One settled full-chain row per contract per session, plus open interest.

    This is the job that makes the cadence buckets safe. LEAPS are never
    sampled intraday, so without a guaranteed daily row they would have no
    series at all — here they get exactly one, which is genuinely all the
    information a contract that re-quotes twice a day contains.
    """
    async with async_session() as db:
        try:
            # Per venue: an underlying is snapshotted only if its own venue
            # had a session today.
            results = await sync_all(db, full=True, only_due=True)
        except Exception:
            logger.exception("EOD snapshot failed")
            return
    if results:
        daily = sum(r.daily_rows for r in results)
        logger.info("EOD snapshot: %s; %d daily rows", _summarise(results), daily)
    else:
        logger.info("EOD snapshot skipped: no venue had a session today")


@background_task("contract_catalog", trigger=CronTrigger(minute="30", hour="12", timezone=timezone.utc))
async def refresh_contract_catalog() -> None:
    """Daily catalogue refresh, including non-session days.

    New expirations get listed while the market is shut. Registering them
    before the open means Monday's first poll already knows what exists,
    rather than discovering a new LEAPS series mid-session and starting its
    history ragged. On a session day the intraday poll has already done this,
    so this run is a cheap no-op safety net.

    It runs in ``catalog_only`` mode, which upserts the contract dimension and
    writes no quotes. An earlier version claimed ``full=False`` achieved that
    on its own, on the theory that no cadence bucket's slot aligns outside the
    gate. It does not: ``should_persist`` is a pure function of tenor and slot
    index and knows nothing about market hours, so the 12:30 UTC run stored a
    frozen pre-market cross-section every day, weekends included. That is 1,169
    NVDA rows on 2026-09-05.
    """
    now = datetime.now(tz=timezone.utc)
    async with async_session() as db:
        try:
            results = await sync_all(db, catalog_only=True, now=now)
        except Exception:
            logger.exception("Contract catalog refresh failed")
            return
    new = sum(r.new_contracts for r in results)
    if new:
        logger.info("Contract catalog: %d newly listed contracts registered", new)


def _ibkr_catalog_trigger() -> CronTrigger | None:
    """Weekday mornings, before the European open. None (disabled) without IBKR_SVC_URL."""
    if not settings.ibkr_svc_url:
        return None
    return CronTrigger(minute="30", hour="5", day_of_week="mon-fri", timezone=timezone.utc)


@background_task("ibkr_catalog", trigger=_ibkr_catalog_trigger)
async def link_ibkr_contracts() -> None:
    """Match every live contract on an IBKR venue (``IBKR_VENUES``) to its conId.

    The IBKR provider can only quote a contract that has one. Contracts listed
    since the venue's own catalog last ran stay unmatched until tomorrow's
    run, which is the intended order: skewer's rows come from the venue, and
    IBKR only supplies the handle to quote them by.
    """
    gateway = get_gateway()
    if gateway is None:
        return
    today = datetime.now(tz=timezone.utc).date()
    async with async_session() as db:
        try:
            reports = await link_contracts(db, gateway, today)
        except Exception:
            logger.exception("IBKR catalog pass failed")
            return
    for r in reports:
        if r.error:
            logger.warning("IBKR catalog %s (%s): %s", r.symbol, r.product, r.error)
        else:
            logger.info(
                "IBKR catalog %s (%s): %d linked (%d changed), %d rows unmatched, "
                "%d ambiguous, %d IBKR contracts not in the venue catalog",
                r.symbol, r.product, r.linked, r.written, r.unmatched_rows,
                r.ambiguous, r.unmatched_ibkr,
            )
