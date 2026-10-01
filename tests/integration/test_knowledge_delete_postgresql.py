"""知识永久删除在 PostgreSQL 上的外键与事务验证（Spec §5.2、AC08）。

PostgreSQL 会检查外键：候选对知识的引用没有级联，必须先解除再删条目，顺序错了
在这里会直接报错，而 SQLite 测试库默认不检查外键。需要设置 YUMI_TEST_POSTGRES_URL。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from homestay_bot.domain.enums import EmployeeRole, KnowledgeCandidateStatus
from homestay_bot.domain.models import (
    AuditLog,
    Employee,
    Job,
    KnowledgeCandidate,
    KnowledgeEmbedding,
    KnowledgeEntry,
    KnowledgeImage,
)
from homestay_bot.routes.knowledge import KnowledgeAdminService
from tests.integration.test_retention_postgresql import pg_engine  # noqa: F401


@pytest.mark.asyncio
async def test_delete_entry_on_postgresql_unlinks_candidate_before_deleting(pg_engine) -> None:  # noqa: F811
    """条目、配图、向量删除，候选解除引用并改回待处理，清理任务与审计同一事务提交。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Employee(id=1, wecom_userid="synthetic", name="合成管理员",
                             role=EmployeeRole.ADMIN, is_active=True))
        session.add(KnowledgeEntry(
            id=1, scope="global", category="停车", question_zh="停哪里", answer_zh="地下一层。",
            question_en="Parking?", answer_en="B1.", keywords=["停车"],
        ))
        await session.flush()
        session.add_all([
            KnowledgeImage(knowledge_entry_id=1, file_id="a" * 32, content_type="image/png",
                           size=10, sort_order=1, created_by=1),
            KnowledgeEmbedding(entry_id=1, language="zh", model="synthetic", content_hash="h",
                               dimensions=2, vector=[0.1, 0.2]),
            KnowledgeCandidate(canonical_key="停哪里", canonical_question="停哪里", category="停车",
                               status=KnowledgeCandidateStatus.CONVERTED, knowledge_entry_id=1,
                               total_occurrences=5, draft_generation=2),
        ])
        await session.commit()

    async with factory() as session:
        await KnowledgeAdminService(session).delete_entry(1, 1)

    async with factory() as session:
        assert list(await session.scalars(select(KnowledgeEntry))) == []
        assert list(await session.scalars(select(KnowledgeImage))) == []
        assert list(await session.scalars(select(KnowledgeEmbedding))) == []
        candidate = await session.scalar(select(KnowledgeCandidate))
        assert candidate.status is KnowledgeCandidateStatus.OPEN
        assert candidate.knowledge_entry_id is None
        assert candidate.draft_generation == 3
        job = await session.scalar(
            select(Job).where(Job.dedupe_key.startswith("knowledge-entry-cleanup:1:"))
        )
        assert job.payload == {"file_ids": ["a" * 32]}
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {"knowledge.delete", "faq_candidate.reopen"} <= actions
