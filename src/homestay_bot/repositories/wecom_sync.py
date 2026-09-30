"""微信客服同步游标的读写：每次读写用独立短事务，与消息处理事务分开。"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from homestay_bot.domain.models import WeComSyncCursor


class SQLAlchemySyncCursorStore:
    """按客服账号保存同步游标；读不到时返回空字符串（企业微信从头同步）。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """注入会话工厂：游标在每页处理完后立即提交，不跟随某条消息的事务回滚。"""
        self._factory = factory

    async def load(self, open_kfid: str) -> str:
        """读取上次同步到的位置。"""
        async with self._factory() as session:
            cursor = await session.scalar(
                select(WeComSyncCursor.cursor).where(WeComSyncCursor.open_kfid == open_kfid)
            )
            return cursor or ""

    async def save(self, open_kfid: str, cursor: str) -> None:
        """记录已处理完的位置；同一账号的同步由调用方串行，这里直接覆盖。"""
        async with self._factory() as session:
            row = await session.get(WeComSyncCursor, open_kfid)
            if row is None:
                session.add(WeComSyncCursor(open_kfid=open_kfid, cursor=cursor))
            else:
                row.cursor = cursor
                row.updated_at = datetime.now(UTC)
            await session.commit()
