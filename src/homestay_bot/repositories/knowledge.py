from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import Language
from homestay_bot.domain.models import KnowledgeEmbedding, KnowledgeEntry
from homestay_bot.services.knowledge_embeddings import (
    PendingVector,
    StoredVector,
    content_hash,
    embedding_text,
)


class SQLAlchemyKnowledgeRepository:
    """使用 SQLAlchemy 读取已审核并启用的民宿知识及其向量。"""

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

    async def list_vectors(self, model: str) -> list[StoredVector]:
        """返回某个模型的全部知识向量；是否仍与正文一致由调用方按哈希判断。"""
        rows = await self._session.scalars(
            select(KnowledgeEmbedding).where(KnowledgeEmbedding.model == model)
        )
        return [
            StoredVector(
                entry_id=row.entry_id,
                language=row.language,
                content_hash=row.content_hash,
                vector=list(row.vector),
            )
            for row in rows
        ]

    async def save_current_vectors(self, model: str, vectors: list[PendingVector]) -> int:
        """只写入仍与知识当前正文一致的向量，返回写入条数；调用方负责提交。

        向量是在事务之外算出来的，期间管理员可能改了正文。这里在同一事务里重新
        读取知识、重算内容哈希，对不上的结果直接丢弃，旧任务晚到不会覆盖新正文。
        知识已被删除时同样丢弃。
        """
        if not vectors:
            return 0
        entries = {
            entry.id: entry
            for entry in await self._session.scalars(
                select(KnowledgeEntry).where(
                    KnowledgeEntry.id.in_({item.entry_id for item in vectors})
                )
            )
        }
        existing = {
            (row.entry_id, row.language): row
            for row in await self._session.scalars(
                select(KnowledgeEmbedding).where(
                    KnowledgeEmbedding.model == model,
                    KnowledgeEmbedding.entry_id.in_(set(entries)),
                )
            )
        }
        saved = 0
        for item in vectors:
            entry = entries.get(item.entry_id)
            if entry is None:
                continue
            current = content_hash(embedding_text(entry, Language(item.language)), model)
            if current != item.content_hash:
                continue
            row = existing.get((item.entry_id, item.language))
            if row is None:
                row = KnowledgeEmbedding(
                    entry_id=item.entry_id,
                    language=item.language,
                    model=model,
                    content_hash=item.content_hash,
                    dimensions=len(item.vector),
                    vector=item.vector,
                )
                self._session.add(row)
                existing[(item.entry_id, item.language)] = row
            else:
                row.content_hash = item.content_hash
                row.dimensions = len(item.vector)
                row.vector = item.vector
            saved += 1
        await self._session.flush()
        return saved
