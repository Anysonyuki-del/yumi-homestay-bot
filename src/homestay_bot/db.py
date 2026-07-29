from collections.abc import AsyncIterator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    """创建带连接健康检查的异步数据库引擎。"""
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and "timeout" not in url.query:
        # 本地轮询与发送会并发写 SQLite，短暂锁冲突应等待而非立即失败。
        url = url.update_query_dict({"timeout": "30"})
    return create_async_engine(url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建关闭自动过期的异步会话工厂。"""
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """为请求或后台任务提供自动关闭的数据库会话。"""
    async with factory() as session:
        yield session
