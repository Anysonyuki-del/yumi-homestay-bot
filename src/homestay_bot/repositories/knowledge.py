from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import KnowledgeEntry


class SQLAlchemyKnowledgeRepository:
    """使用 SQLAlchemy 读取已审核并启用的民宿知识。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前数据库会话。"""
        self._session = session

    async def list_active(self) -> list[KnowledgeEntry]:
        """按稳定主键顺序返回已启用知识，排除全部停用内容。"""
        statement = (
            select(KnowledgeEntry)
            .where(KnowledgeEntry.is_enabled.is_(True))
            .order_by(KnowledgeEntry.id)
        )
        return list((await self._session.scalars(statement)).all())
