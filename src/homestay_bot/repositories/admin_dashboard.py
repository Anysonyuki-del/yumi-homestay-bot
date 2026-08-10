"""管理员总览一致读事务准备。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SQLAlchemyAdminDashboardRepository:
    """为多次只读聚合建立数据库级一致快照。"""

    def __init__(self, session: AsyncSession) -> None:
        """保存总览请求独占的短会话。"""
        self._session = session
        self._prepared = False

    async def prepare_consistent_read(self) -> None:
        """在任何业务查询前开启只读一致快照。"""
        if self._prepared:
            return
        if self._session.in_transaction():
            raise RuntimeError("总览一致读必须在新事务的第一条语句设置")
        dialect_name = self._session.get_bind().dialect.name
        if dialect_name == "postgresql":
            await self._session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
        elif dialect_name == "sqlite":
            # aiosqlite 的 legacy transaction control 不会为 SELECT 自动发出 BEGIN；
            # 显式开启事务后，首项业务读取会固定 WAL 读快照。
            await self._session.execute(text("BEGIN"))
        else:
            await self._session.execute(text("SELECT 1"))
        self._prepared = True
