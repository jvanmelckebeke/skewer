"""Adding an underlying whose venue code differs from its ticker.

Eurex lists ArcelorMittal (Yahoo ``MT.AS``) under the product ``ISPA``, and
Euronext's Ahold is ``AH`` against a ticker of ``AD.AS``. ``venue_product``
records that mapping, and validation is the step that has to honour it: it is
what decides whether the row is created, so validating the bare ticker rejects
exactly the underlyings the field exists for.
"""

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.domain.venue import Venue
from app.services import underlying_service


class FakeProvider:
    """A venue that knows one product code and nothing about tickers."""

    def __init__(self, product: str, name: str):
        self._product = product
        self._name = name
        self.seen: list[tuple[str, str | None]] = []

    async def validate(self, symbol: str, product: str | None = None):
        self.seen.append((symbol, product))
        if product == self._product:
            return True, self._name
        return False, ""


@pytest_asyncio.fixture
async def default_group(db):
    """The 'Watchlist' row the initial migration seeds.

    `create_all` builds the schema but runs no data migrations, so a SQLite
    test starts with no group at all and `add_underlying` has nothing to file
    the new row under.
    """
    from app.models import Group

    group = Group(name="Watchlist", is_default=True, position=0)
    db.add(group)
    await db.commit()
    return group


@pytest.fixture
def eurex(monkeypatch):
    provider = FakeProvider("ISPA", "ARCELORMITTAL")
    monkeypatch.setattr(
        underlying_service, "for_venue", lambda venue: provider
    )
    return provider


async def test_venue_product_reaches_the_validator(db, default_group, eurex):
    row = await underlying_service.add_underlying(
        db, "MT.AS", venue=Venue.EUREX, venue_product="ISPA"
    )

    assert row.symbol == "MT.AS"
    assert row.name == "ARCELORMITTAL"
    assert row.venue_product == "ISPA"
    assert eurex.seen == [("MT.AS", "ISPA")]


async def test_the_bare_ticker_is_still_rejected(db, eurex):
    """The regression: without the code, MT.AS is not an ISPA option series."""
    with pytest.raises(HTTPException) as excinfo:
        await underlying_service.add_underlying(db, "MT.AS", venue=Venue.EUREX)

    assert excinfo.value.status_code == 422
    assert eurex.seen == [("MT.AS", None)]
