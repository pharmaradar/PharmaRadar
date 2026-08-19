"""Test fixtures using testcontainers (real Postgres — never mock the DB)."""
import asyncio
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from app.database import Base

# Importing the models is what registers them on Base.metadata; without it
# create_all() builds an empty schema and every query fails with
# "relation does not exist".
import app.models  # noqa: F401,E402


@pytest.fixture(scope="session")
def pg_container():
    # pgvector, not plain postgres:16. ExtractedInsight.embedding is a
    # Vector(512) column, so create_all() fails with 'type "vector" does not
    # exist' on the stock image — the same reason production runs the pgvector
    # image rather than the native Railway plugin.
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg


@pytest.fixture(scope="session")
def db_url(pg_container):
    url = pg_container.get_connection_url()
    # testcontainers returns psycopg2 URL; swap driver for asyncpg
    return url.replace("psycopg2", "asyncpg").replace("postgresql://", "postgresql+asyncpg://")


# NOTE: no `event_loop` override here.
#
# pytest-asyncio 0.23+ deprecated overriding it, and each test now gets its own
# loop. A SESSION-scoped engine therefore held asyncpg connections belonging to
# a loop that had already closed, and the first test to actually use the
# database died with "InternalClientError: got result for unknown protocol
# state 3". That went unnoticed because no test used db_session until now — the
# whole suite was pure-logic and mocked.
#
# The engine is function-scoped so its connections live in the same loop as the
# test using them. The testcontainer stays session-scoped, since starting
# Postgres is the expensive part, not creating an engine.
@pytest_asyncio.fixture
async def db_engine(db_url):
    engine = create_async_engine(db_url, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    AsyncTestSession = async_sessionmaker(db_engine, expire_on_commit=False)
    async with AsyncTestSession() as session:
        yield session
        await session.rollback()
