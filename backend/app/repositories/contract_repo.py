import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.contracts import ContractRef
from app.models import OptionContract
from app.services.ibkr.matching import CatalogRow

logger = logging.getLogger(__name__)

# One bind parameter per column per row against PostgreSQL's hard 32767-per-
# statement ceiling. A full chain is ~3.3k contracts x 9 columns = ~30k
# parameters, which clears the limit by a margin too thin to rely on — SPY's
# chain is several times larger. Chunking is not optional here; fibenchi
# learned the same lesson when a whole-history re-fetch silently failed on
# exactly the symbols with the most data.
UPSERT_CHUNK_ROWS = 1000


def _chunked(rows: list[dict], size: int) -> Iterator[list[dict]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


class ContractRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_for_underlying(
        self, underlying_id: int, live_on: date | None = None
    ) -> list[ContractRef]:
        """Contract refs for an underlying, optionally only unexpired ones.

        Returns ``ContractRef`` rather than ORM rows for the same reason
        fibenchi returns ``AssetRef``: the sync loop commits and rolls back
        per underlying, and a rollback expires every live ORM instance in the
        session. A frozen ref captured while the row was live cannot expire.
        """
        stmt = select(
            OptionContract.id,
            OptionContract.symbol,
            OptionContract.venue,
            OptionContract.expiration,
            OptionContract.strike,
            OptionContract.right,
            OptionContract.exercise_style,
            OptionContract.contract_size,
            OptionContract.currency,
        ).where(OptionContract.underlying_id == underlying_id)
        if live_on is not None:
            stmt = stmt.where(OptionContract.expiration >= live_on)
        result = await self.db.execute(
            stmt.order_by(OptionContract.expiration, OptionContract.strike)
        )
        # Built from the stored columns rather than by re-parsing the symbol.
        # That used to be `ContractRef.parse(occ)`, which only worked while
        # every symbol was an OCC one that encodes its own terms. A Eurex key
        # is an opaque ISIN, so the terms have to come from where they were
        # written — which is also strictly cheaper than re-parsing.
        return [
            ContractRef(
                symbol=symbol,
                venue=venue,
                root=symbol.split(":")[-1],
                expiration=expiration,
                right=right,
                strike=float(strike),
                exercise_style=exercise_style,
                contract_size=int(contract_size),
                currency=currency,
                id=cid,
            )
            for (cid, symbol, venue, expiration, strike, right,
                 exercise_style, contract_size, currency) in result.all()
        ]

    async def upsert_many(
        self, underlying_id: int, refs: list, seen_at: datetime
    ) -> dict[str, int]:
        """Insert new contracts, refresh ``last_seen_at`` on existing ones.

        Returns ``{symbol: contract_id}`` for every contract passed in —
        which is what the quote writer needs, since a fact row keys on the
        surrogate id. Existing rows are *not* otherwise updated: strike,
        expiration and right are immutable properties of the contract, so a
        provider disagreeing about them is a fault to reject (handled at parse
        time for OCC) rather than an update to apply.

        Eurex's ``VersionNr`` is the one thing that genuinely can change a
        contract's terms, when a corporate action adjusts them. The adjusted
        contract keeps a distinct ISIN, so it still arrives here as a new row.
        What is no longer lost is the link back: the version columns are
        stored, and :meth:`link_adjusted` joins the new row to the one it
        replaced. See that method for why it refuses an ambiguous match.
        """
        if not refs:
            return {}

        rows = [
            {
                "underlying_id": underlying_id,
                "symbol": r.ref.symbol,
                "venue": r.ref.venue,
                "exercise_style": r.ref.exercise_style,
                "expiration": r.ref.expiration,
                "strike": r.ref.strike,
                "right": r.ref.right,
                "contract_size": r.contract_size,
                "currency": r.currency,
                "version_nr": getattr(r, "version_nr", None),
                "generation_nr": getattr(r, "generation_nr", None),
                "original_strike": getattr(r, "original_strike", None),
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
            }
            for r in refs
        ]

        for chunk in _chunked(rows, UPSERT_CHUNK_ROWS):
            stmt = pg_insert(OptionContract).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol"],
                set_={
                    "last_seen_at": stmt.excluded.last_seen_at,
                    # Refreshed rather than left alone, unlike the terms. A
                    # contract's own version never changes (an adjustment
                    # issues a new ISIN), so this only ever fills in rows
                    # stored before the columns existed.
                    "version_nr": stmt.excluded.version_nr,
                    "generation_nr": stmt.excluded.generation_nr,
                    "original_strike": stmt.excluded.original_strike,
                },
            )
            await self.db.execute(stmt)
        await self.db.commit()

        # Only worth a query when the venue publishes versions at all and one
        # of them is non-zero, which on live data is 4 contracts out of 1,288
        # on SAP and none at all on any other product tested.
        if any((getattr(r, "version_nr", None) or 0) > 0 for r in refs):
            await self.link_adjusted(underlying_id)

        symbols = [r.ref.symbol for r in refs]
        return await self.ids_for(symbols)

    async def link_adjusted(self, underlying_id: int) -> int:
        """Point each adjusted contract at the row it replaced.

        A corporate action gives the contract a new ISIN, a new strike and
        sometimes a new contract size. What identifies its predecessor is
        ``OriginalStrike``: the adjusted SAP contract carries strike 91.49 and
        original strike 92, and the row it replaced was struck at 92.

        Matching on the original strike alone is not enough. Eurex also lists
        *fresh* contracts at round strikes, so a live unadjusted contract at
        92 would match just as well. ``GenerationNr`` separates them: on SAP
        the adjusted contracts sit at generation 7 while every unadjusted one
        is at generation 8, so the predecessor is the row at the same
        generation and one version lower.

        That generation rule is read off live data, not off documentation, so
        it is applied conservatively: a match is only written when exactly one
        candidate exists. Two candidates, or none, leaves the link NULL, which
        is the same state as "this contract was never adjusted" and reads the
        same way — as no claim rather than a wrong one.

        Returns the number of links written.
        """
        newer = aliased(OptionContract)
        older = aliased(OptionContract)

        # Candidates, grouped so ambiguity can be detected before writing.
        pairs = (
            select(
                newer.id.label("new_id"),
                func.min(older.id).label("old_id"),
                func.count(older.id).label("matches"),
            )
            .join(
                older,
                and_(
                    older.underlying_id == newer.underlying_id,
                    older.expiration == newer.expiration,
                    older.right == newer.right,
                    older.strike == newer.original_strike,
                    older.generation_nr == newer.generation_nr,
                    older.version_nr == newer.version_nr - 1,
                    older.id != newer.id,
                ),
            )
            .where(
                newer.underlying_id == underlying_id,
                newer.version_nr > 0,
                newer.supersedes_contract_id.is_(None),
                newer.original_strike.is_not(None),
                newer.generation_nr.is_not(None),
            )
            .group_by(newer.id)
            .subquery()
        )

        ambiguous = await self.db.execute(
            select(func.count()).select_from(pairs).where(pairs.c.matches > 1)
        )
        count = int(ambiguous.scalar_one())
        if count:
            logger.warning(
                "%d adjusted contracts on underlying %d matched more than one "
                "predecessor; leaving them unlinked",
                count, underlying_id,
            )

        result = await self.db.execute(
            update(OptionContract)
            .where(
                OptionContract.id == pairs.c.new_id,
                pairs.c.matches == 1,
            )
            .values(supersedes_contract_id=pairs.c.old_id)
        )
        await self.db.commit()
        written = int(result.rowcount or 0)
        if written:
            logger.info(
                "Linked %d adjusted contracts to their predecessors on underlying %d",
                written, underlying_id,
            )
        return written

    async def ids_for(self, symbols: list[str]) -> dict[str, int]:
        """Map contract symbols to contract ids, chunked to stay under the
        bind-parameter ceiling for very large chains."""
        out: dict[str, int] = {}
        for start in range(0, len(symbols), UPSERT_CHUNK_ROWS):
            batch = symbols[start:start + UPSERT_CHUNK_ROWS]
            result = await self.db.execute(
                select(OptionContract.symbol, OptionContract.id).where(
                    OptionContract.symbol.in_(batch)
                )
            )
            out.update({sym: cid for sym, cid in result.all()})
        return out

    async def count_for(self, underlying_id: int, live_on: date | None = None) -> int:
        stmt = select(func.count()).select_from(OptionContract).where(
            OptionContract.underlying_id == underlying_id
        )
        if live_on is not None:
            stmt = stmt.where(OptionContract.expiration >= live_on)
        return int((await self.db.execute(stmt)).scalar_one())

    async def catalog_rows(self, underlying_id: int, live_on: date) -> list[CatalogRow]:
        """The underlying's contracts that the venue's latest catalog listed.

        "Latest" is the day of the newest ``last_seen_at``: a contract the
        venue stopped listing keeps its old terms and would otherwise stay a
        match candidate, which is how a superseded Eurex series could claim
        the conId of its adjusted replacement.
        """
        newest = await self.db.scalar(
            select(func.max(OptionContract.last_seen_at)).where(
                OptionContract.underlying_id == underlying_id
            )
        )
        if newest is None:
            return []
        result = await self.db.execute(
            select(
                OptionContract.id, OptionContract.expiration, OptionContract.strike,
                OptionContract.right, OptionContract.contract_size,
            ).where(
                OptionContract.underlying_id == underlying_id,
                OptionContract.expiration >= live_on,
                OptionContract.last_seen_at >= newest - timedelta(days=1),
            )
        )
        return [CatalogRow(*row) for row in result.all()]

    async def set_ibkr_con_ids(self, links: dict[int, int]) -> int:
        """Write ``{contract id: conId}``; returns how many rows changed."""
        ids = list(links)
        current: dict[int, int | None] = {}
        for start in range(0, len(ids), UPSERT_CHUNK_ROWS):
            result = await self.db.execute(
                select(OptionContract.id, OptionContract.ibkr_con_id).where(
                    OptionContract.id.in_(ids[start:start + UPSERT_CHUNK_ROWS])
                )
            )
            current.update(dict(result.all()))
        changed = [
            {"id": cid, "ibkr_con_id": con_id}
            for cid, con_id in links.items()
            if cid in current and current[cid] != con_id
        ]
        if changed:
            await self.db.execute(update(OptionContract), changed)
        await self.db.commit()
        return len(changed)
