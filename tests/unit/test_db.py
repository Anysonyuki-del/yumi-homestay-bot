import pytest

from homestay_bot.db import create_engine


@pytest.mark.asyncio
async def test_sqlite_engine_waits_for_concurrent_writer() -> None:
    """本地 SQLite 应等待短暂写锁，避免立即让后台 worker 退出。"""
    engine = create_engine("sqlite+aiosqlite:///test.db")
    try:
        _, connect_options = engine.sync_engine.dialect.create_connect_args(engine.url)
    finally:
        await engine.dispose()

    assert connect_options["timeout"] == 30.0
