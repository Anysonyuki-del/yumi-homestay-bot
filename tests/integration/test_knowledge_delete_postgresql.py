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


@pytest.mark.asyncio
@pytest.mark.parametrize("round_number", range(5))
async def test_concurrent_delete_and_upload_leave_no_unowned_file(
    pg_engine, tmp_path, round_number  # noqa: F811
) -> None:
    """删除与上传真正并发：两边都锁同一条目而串行。无论谁先，私有目录里的图片
    要么已被上传失败时的补偿删掉，要么出现在删除登记的清理任务里，不会成为孤儿文件。
    """
    import asyncio
    import io

    from homestay_bot.application import SessionKnowledgeAdminService
    from homestay_bot.services.private_file_storage import PrivateFileStorage

    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 17 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Employee(id=1, wecom_userid="synthetic", name="合成管理员",
                             role=EmployeeRole.ADMIN, is_active=True))
        session.add(KnowledgeEntry(
            id=1, scope="global", category="停车", question_zh="停哪里", answer_zh="地下一层。",
            question_en="Parking?", answer_en="B1.", keywords=["停车"],
        ))
        await session.commit()
    storage = PrivateFileStorage(tmp_path / "private")
    service = SessionKnowledgeAdminService(factory, storage)

    results = await asyncio.gather(
        service.upload_image(1, 1, io.BytesIO(png), "image/png"),
        service.delete_entry(1, 1),
        return_exceptions=True,
    )

    assert results[1] is None, results  # 删除总能完成
    on_disk = {path.name for path in (tmp_path / "private").iterdir()} if (
        tmp_path / "private"
    ).exists() else set()
    async with factory() as session:
        assert list(await session.scalars(select(KnowledgeEntry))) == []
        assert list(await session.scalars(select(KnowledgeImage))) == []
        queued = {
            file_id
            for job in await session.scalars(select(Job))
            for file_id in job.payload.get("file_ids", [])
        }
    # 上传赢了锁：文件随删除登记了清理；上传输了锁：补偿已删除文件，目录为空。
    assert on_disk <= queued, (round_number, results, on_disk, queued)


@pytest.mark.asyncio
@pytest.mark.parametrize("round_number", range(5))
async def test_concurrent_copy_and_source_delete_leave_no_dangling_image(
    pg_engine, tmp_path, round_number  # noqa: F811
) -> None:
    """跨房复制与删除源条目真正并发（知识库重构 Spec §5.2）：两边都锁源条目而串行。

    无论谁先：库里每条配图记录指向的文件都在磁盘上（没有悬空引用）；磁盘上每个文件
    要么被配图记录引用，要么在删除登记的清理任务里（没有无主孤儿）。复制成功时副本是
    停用草稿；复制失败只能是「源已变化」或「源不存在」。
    """
    import asyncio
    import io

    from homestay_bot.application import SessionKnowledgeAdminService
    from homestay_bot.domain.models import PropertyProfile
    from homestay_bot.routes.knowledge import CopyResult, KnowledgeFormError
    from homestay_bot.services.private_file_storage import PrivateFileStorage

    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 17 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    storage = PrivateFileStorage(tmp_path / "private")
    stored = await storage.save_image(io.BytesIO(png), "image/png", 10_000)
    async with factory() as session:
        session.add(Employee(id=1, wecom_userid="synthetic", name="合成管理员",
                             role=EmployeeRole.ADMIN, is_active=True))
        session.add_all([PropertyProfile(id=1, title="合成201房"),
                         PropertyProfile(id=2, title="合成202房")])
        await session.flush()
        # 不显式指定编号：PostgreSQL 序列不会因显式编号前进，副本插入会撞主键。
        source = KnowledgeEntry(
            scope="property", property_id=1, category="停车", question_zh="停哪里",
            answer_zh="地下一层。", question_en="Parking?", answer_en="B1.", keywords=["停车"],
        )
        session.add(source)
        await session.flush()
        source_id = source.id
        session.add(KnowledgeImage(knowledge_entry_id=source_id, file_id=stored.file_id,
                                   content_type="image/png", size=len(png), sort_order=1,
                                   created_by=1))
        await session.commit()
    service = SessionKnowledgeAdminService(factory, storage)

    results = await asyncio.gather(
        service.copy_to_rooms(source_id, 1, [2], copy_images=True),
        service.delete_entry(source_id, 1),
        return_exceptions=True,
    )

    assert results[1] is None, results  # 删除总能完成
    copied = results[0]
    assert isinstance(copied, CopyResult | KnowledgeFormError | LookupError), results
    on_disk = {path.name for path in (tmp_path / "private").iterdir()}
    async with factory() as session:
        entries = list(await session.scalars(select(KnowledgeEntry)))
        referenced = set(await session.scalars(select(KnowledgeImage.file_id)))
        queued = {
            file_id
            for job in await session.scalars(select(Job))
            for file_id in job.payload.get("file_ids", [])
        }
    assert referenced <= on_disk, (round_number, results)
    assert on_disk <= referenced | queued, (round_number, results, on_disk)
    if isinstance(copied, CopyResult):
        assert [(entry.property_id, entry.is_enabled) for entry in entries] == [(2, False)]
        assert stored.file_id not in referenced
    else:
        assert entries == []
        assert on_disk <= {stored.file_id}
