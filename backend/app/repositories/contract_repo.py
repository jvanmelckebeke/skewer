import logging
from collections.abc import Iterator
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import ContractRef
from app.models import OptionContract

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
        contract's terms, when a corporate action adjusts them. It is not
        handled yet: an adjusted contract keeps a distinct ISIN, so it arrives
        here as a *new* row rather than as a mutation of the old one, which is
        wrong in a subtle way — the two are economically the same position.
        Tracked as a known gap rather than silently patched over.
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
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
            }
            for r in refs
        ]

        for chunk in _chunked(rows, UPSERT_CHUNK_ROWS):
            stmt = pg_insert(OptionContract).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol"],
                set_={"last_seen_at": stmt.excluded.last_seen_at},
            )
            await self.db.execute(stmt)
        await self.db.commit()

        symbols = [r.ref.symbol for r in refs]
        return await self.ids_for(symbols)

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
        from sqlalchemy import func

        stmt = select(func.count()).select_from(OptionContract).where(
            OptionContract.underlying_id == underlying_id
        )
        if live_on is not None:
            stmt = stmt.where(OptionContract.expiration >= live_on)
        return int((await self.db.execute(stmt)).scalar_one())
