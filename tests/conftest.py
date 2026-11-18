from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from homestay_bot.domain.models import Base


@pytest_asyncio.fixture
async def sqlite_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """每个用例独占一个内存库，在事件循环关闭前释放连接和 SQLite 工作线程。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
