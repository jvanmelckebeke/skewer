"""Test fixtures.

Tests run against SQLite in-memory — no database container needed, same as
fibenchi. The tradeoff is explicit: SQLite cannot express hypertables or
``ON CONFLICT`` on a Postgres dialect insert, so repository-level upsert paths
are exercised against Postgres in ``test_integration.py`` (skipped unless
``SKEWER_TEST_DATABASE_URL`` is set) and the pure logic — which is where the
option-specific correctness lives — is tested here.
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ``app.models`` is imported for its side effect: a mapped class has to have
# been imported before ``Base.metadata`` knows its table. Without it a test
# module that happens not to import any model gets an empty schema and fails
# on "no such table", several frames from the cause.
import app.models  # noqa: F401
from app.database import Base, get_db

TEST_DB_URL = os.getenv("SKEWER_TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    # drop_all before create_all, not just create_all. An in-memory SQLite
    # database is new for every test so state cannot leak; a PostgreSQL one is
    # shared, and without the drop the second test to insert 'TEST' hits a
    # unique-constraint violation from the first test's leftovers.
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncSession:
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(db):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anyio_backend():
    return "asyncio"
