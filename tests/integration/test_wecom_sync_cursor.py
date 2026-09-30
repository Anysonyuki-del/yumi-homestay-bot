"""微信客服同步游标的数据库存储：读不到为空、再次保存覆盖。"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.models import Base
from homestay_bot.repositories.wecom_sync import SQLAlchemySyncCursorStore


async def test_cursor_store_returns_empty_then_latest_saved_cursor(tmp_path):
    """首次读取为空（企业微信从头同步）；保存两次后读到的是最后一次。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cursor.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = SQLAlchemySyncCursorStore(async_sessionmaker(engine, expire_on_commit=False))

    assert await store.load("wk-1") == ""
    await store.save("wk-1", "c1")
    await store.save("wk-1", "c2")

    assert await store.load("wk-1") == "c2"
    assert await store.load("wk-other") == ""
    await engine.dispose()
