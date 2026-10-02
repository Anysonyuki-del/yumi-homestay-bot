from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, make_url
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
    engine = create_async_engine(url, pool_pre_ping=True)
    if url.get_backend_name() == "sqlite":
        @event.listens_for(engine.sync_engine, "savepoint")
        def _ensure_sqlite_parent_transaction(connection: Connection, name: str | None) -> None:
            """让保存点属于真实父事务，防止旧式 SQLite 驱动在 RELEASE 时提前提交。"""
            # 普通查询只开启 SQLAlchemy 逻辑事务；仅在保存点前补 BEGIN，避免扩大
            # 不使用保存点的只读事务锁范围。已有物理事务时不得重复 BEGIN。
            driver_connection = connection.connection.driver_connection
            assert driver_connection is not None, "保存点必须使用有效的 SQLite 驱动连接"
            if not driver_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")

        # SQLite 默认关闭外键校验；显式开启，确保本地行为与 PostgreSQL 一致。
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(
            dbapi_connection: Any,
            connection_record: Any,
        ) -> None:
            """为每个 SQLite 连接开启外键约束。"""
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建关闭自动过期的异步会话工厂。"""
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """为请求或后台任务提供自动关闭的数据库会话。"""
    async with factory() as session:
        yield session
