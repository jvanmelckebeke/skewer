"""Intraday quotes from IBKR, through the shared ibkr-svc gateway.

One instance per venue that ``IBKR_VENUES`` enables (Eurex today); the venue
sets the feed delay and the currency through its ``VenueSpec``.

IBKR has no chain call: every contract is its own market-data request, and
the gateway's ~100 lines are shared with fibenchi. So unlike the other
providers this one is not asked for a whole chain. It is handed the subset
the cadence grid wants on this slot and returns exactly those quotes.

What makes a quote usable here:

* **Its market-data type matches the venue's feed.** ``VenueSpec.feed_delay``
  says whether the feed is live or 15 minutes late, and every row is stamped
  on that assumption. A ``delayed_frozen`` quote, or a live one arriving on a
  delayed venue, would land in the wrong slot, so it is dropped.
* **It arrived inside its slot's window.** The poll is stamped
  ``floor(start - delay)``; a quote received after the next slot's window
  opened describes the next slot, not this one.
* **It is a read, not an error.** A quote with ``error`` set carries nothing.

A null bid is kept as null: the service maps IB's "nothing" and anything
<= 0 to null, which is the zero-bid rule already applied. A timed-out read
keeps whichever side arrived, and ``mid_price`` needs both.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.domain.contracts import ContractRef
from app.domain.venue import Venue, spec_for
from app.services.ibkr.base import IbkrGateway, Quote, SnapshotJob
from app.services.option_providers.base import ProviderUnavailable, RawChain, RawOptionQuote

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0
# A 3.5k-contract slot took about 3.5 minutes live. Past this the poll is
# already outside its own slot, so waiting longer buys nothing.
JOB_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class Quotable:
    """A stored contract the IBKR provider can quote: its identity and conId."""

    ref: ContractRef
    con_id: int


class IbkrOptionProvider:
    """Quotes a given subset of one underlying's contracts on one slot."""

    def __init__(self, venue: Venue, gateway: IbkrGateway) -> None:
        self.venue = venue
        self._gateway = gateway
        self._delay = spec_for(self.venue).feed_delay
        self._accepted_type = "delayed" if self._delay > timedelta(0) else "live"

    async def _run(self, con_ids: list[int], underlying: int) -> SnapshotJob:
        job = await self._gateway.submit_snapshots(con_ids, underlying, priority="bulk")
        deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
        while job.status == "running":
            if time.monotonic() > deadline:
                raise ProviderUnavailable(f"ibkr-svc snapshot job {job.id} still running")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            job = await self._gateway.snapshot_job(job.id)
        if job.status != "done":
            raise ProviderUnavailable(f"ibkr-svc snapshot job {job.id} failed: {job.error}")
        return job

    def _usable(self, q: Quote, window_end: datetime) -> str | None:
        """Why ``q`` is dropped, or None if it is kept."""
        if q.error:
            return "error"
        if q.market_data_type != self._accepted_type:
            return f"type:{q.market_data_type}"
        if q.receive_time is None or q.receive_time >= window_end:
            return "late"
        return None

    async def fetch_subset(
        self, symbol: str, under_con_id: int, contracts: list[Quotable],
        slot: datetime, base_minutes: int,
    ) -> RawChain:
        """Quote ``contracts`` for ``slot``; the rows are for the persist path."""
        job = await self._run([c.con_id for c in contracts], under_con_id)
        # The feed shows slot..slot+base during slot+delay..slot+base+delay.
        window_end = slot + timedelta(minutes=base_minutes) + self._delay

        by_con_id = {c.con_id: c.ref for c in contracts}
        dropped: Counter[str] = Counter()
        quotes: list[RawOptionQuote] = []
        for q in job.quotes:
            ref = by_con_id.get(q.conId)
            if ref is None:
                dropped["unknown_conid"] += 1
                continue
            reason = self._usable(q, window_end)
            if reason:
                dropped[reason] += 1
                continue
            quotes.append(RawOptionQuote(
                ref=ref, bid=q.bid, ask=q.ask, last=q.last,
                last_trade_at=q.last_trade_time, volume=int(q.volume or 0),
                # Open interest comes from Eurex, not from here.
                open_interest=None, iv_provider=None,
                contract_size=ref.contract_size, currency=ref.currency,
            ))
        if dropped:
            logger.warning(
                "%s: IBKR slot %s kept %d of %d quotes; dropped %s",
                symbol, slot.isoformat(), len(quotes), len(job.quotes), dict(dropped),
            )

        spot, bid, ask, close = self._spot(job.underlying, window_end)
        return RawChain(
            symbol=symbol,
            name=symbol,
            currency=contracts[0].ref.currency if contracts else spec_for(self.venue).currency,
            # The spot is the options' own underlying on IBKR, read in the
            # same job, so the two cannot be in different currencies.
            spot_currency=None,
            venue=self.venue,
            spot=spot, bid=bid, ask=ask, previous_close=close,
            market_state=None,
            session_date=slot.astimezone(spec_for(self.venue).expiry_tz).date(),
            fetched_at=datetime.now(tz=timezone.utc),
            expirations=sorted({q.ref.expiration for q in quotes}),
            quotes=quotes,
        )

    def _spot(
        self, samples: list[Quote], window_end: datetime
    ) -> tuple[float | None, float | None, float | None, float | None]:
        """Spot as the mean of the usable underlying reads that bracket the job.

        Each read's price is its mid when both sides are there, else its last
        trade. Averaging the before and after reads puts the spot at the middle
        of the batch rather than at one end of it.
        """
        usable = [s for s in samples if self._usable(s, window_end) is None]
        prices = []
        for s in usable:
            if s.bid is not None and s.ask is not None:
                prices.append((s.bid + s.ask) / 2)
            elif s.last is not None:
                prices.append(s.last)
        spot = sum(prices) / len(prices) if prices else None
        first = usable[0] if usable else None
        return (
            spot,
            first.bid if first else None,
            first.ask if first else None,
            first.close if first else None,
        )
