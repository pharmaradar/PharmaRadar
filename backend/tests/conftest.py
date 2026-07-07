"""Test fixtures using testcontainers (real Postgres — never mock the DB)."""
import asyncio
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from testcontainers.postgres import PostgresContainer

from app.database import Base


@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="session")
def db_url(pg_container):
    url = pg_container.get_connection_url()
    # testcontainers returns psycopg2 URL; swap driver for asyncpg
    return url.replace("psycopg2", "asyncpg").replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db_engine(db_url):
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    AsyncTestSession = async_sessionmaker(db_engine, expire_on_commit=False)
    async with AsyncTestSession() as session:
        yield session
        await session.rollback()
