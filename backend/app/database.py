from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# FastAPI engine — small pool, shared across the long-running uvicorn event loop
engine = create_async_engine(
    settings.async_database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

class CelerySessionLocal:
    """Drop-in async context manager for Celery tasks.

    Creates a brand-new engine (NullPool) on every `async with` — no shared
    state across asyncio.run() calls or forked processes. Disposes the engine
    (closes the single connection) on exit, leaving zero dangling resources.

    Usage:
        async with CelerySessionLocal() as sess:
            obj = await sess.get(MyModel, pk)
    """

    async def __aenter__(self) -> AsyncSession:
        self._engine = create_async_engine(settings.async_database_url, poolclass=NullPool, echo=False)
        factory = async_sessionmaker(self._engine, class_=AsyncSession,
                                     expire_on_commit=False, autocommit=False, autoflush=False)
        self._session = factory()
        await self._session.__aenter__()
        return self._session

    async def __aexit__(self, *args):
        await self._session.__aexit__(*args)
        await self._engine.dispose()


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
