from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    KnowledgeCandidateDraftStatus,
    KnowledgeCandidateStatus,
)
from homestay_bot.domain.models import Base, KnowledgeEntry
from homestay_bot.repositories.faq_candidates import (
    SQLAlchemyFaqCandidateRepository,
)


@pytest.fixture
async def repository():
    """创建使用独立内存数据库的候选仓储。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield SQLAlchemyFaqCandidateRepository(session), session
    await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_creation_is_idempotent_and_context_excludes_inactive(
    repository,
) -> None:
    """相同标准问题应复用候选，模型上下文只能看到可统计候选。"""
    candidates, session = repository
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)

    first = await candidates.get_or_create(
        canonical_question="民宿是否提供停车位？",
        category="交通",
    )
    repeated = await candidates.get_or_create(
        canonical_question="  民宿是否提供停车位?  ",
        category="其他",
    )
    hidden = await candidates.get_or_create(
        canonical_question="是否可以寄存行李？",
        category="服务",
    )
    await candidates.snooze(hidden.id, until=now + timedelta(days=30))
    await session.commit()

    context = await candidates.list_context(now=now)

    assert repeated.id == first.id
    assert repeated.category == "交通"
    assert [(item.id, item.canonical_question) for item in context] == [
        (first.id, "民宿是否提供停车位？")
    ]


@pytest.mark.asyncio
async def test_occurrences_are_idempotent_counted_in_window_and_keep_three_examples(
    repository,
) -> None:
    """来源消息只能计数一次，窗口统计含边界且示例最多保存三条。"""
    candidates, session = repository
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)
    candidate = await candidates.get_or_create(
        canonical_question="民宿是否提供停车位？",
        category="交通",
    )

    for index, hours_ago in enumerate((73, 72, 2, 1, 0), start=1):
        added = await candidates.add_occurrence(
            candidate.id,
            source_message_id=f"msg-{index}",
            occurred_at=now - timedelta(hours=hours_ago),
            example=f"参考问法 {index}",
        )
        assert added is True
    duplicate = await candidates.add_occurrence(
        candidate.id,
        source_message_id="msg-5",
        occurred_at=now,
        example="不应重复",
    )
    await session.commit()

    recent = await candidates.count_since(
        candidate.id,
        since=now - timedelta(hours=72),
        until=now,
    )
    refreshed = await candidates.get(candidate.id)

    assert duplicate is False
    assert recent == 4
    assert refreshed is not None
    assert refreshed.total_occurrences == 5
    assert refreshed.examples == [
        "参考问法 1",
        "参考问法 2",
        "参考问法 3",
    ]
    assert refreshed.examples_version == 3


@pytest.mark.asyncio
async def test_snooze_clears_private_content_and_reopens_after_thirty_days(
    repository,
) -> None:
    """关闭期间不计数，期满重开后从新的阈值基线继续累计。"""
    candidates, session = repository
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)
    candidate = await candidates.get_or_create(
        canonical_question="能否寄存行李？",
        category="服务",
    )
    await candidates.add_occurrence(
        candidate.id,
        source_message_id="msg-before-close",
        occurred_at=now,
        example="能帮我寄存一下吗",
    )
    await candidates.mark_draft_ready(
        candidate.id,
        {
            "question_zh": "能否寄存行李？",
            "answer_zh": "【待管理员确认】",
        },
    )
    await candidates.snooze(candidate.id, until=now + timedelta(days=30))

    ignored = await candidates.add_occurrence(
        candidate.id,
        source_message_id="msg-during-close",
        occurred_at=now + timedelta(days=1),
        example="关闭期间不应保存",
    )
    reopened = await candidates.list_context(now=now + timedelta(days=30))
    await session.commit()
    refreshed = await candidates.get(candidate.id)

    assert ignored is False
    assert [item.id for item in reopened] == [candidate.id]
    assert refreshed is not None
    assert refreshed.status is KnowledgeCandidateStatus.OPEN
    assert refreshed.examples == []
    assert refreshed.draft_payload is None
    assert refreshed.draft_status is KnowledgeCandidateDraftStatus.NONE
    assert refreshed.last_threshold_total == 1
    assert await candidates.count_since(
        candidate.id,
        since=now - timedelta(days=1),
        until=now + timedelta(days=31),
    ) == 0


@pytest.mark.asyncio
async def test_draft_state_conversion_and_occurrence_pruning(repository) -> None:
    """草稿状态可持久化，转换知识后清除隐私数据并支持过期明细清理。"""
    candidates, session = repository
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)
    candidate = await candidates.get_or_create(
        canonical_question="是否提供儿童用品？",
        category="设施",
    )
    await candidates.add_occurrence(
        candidate.id,
        source_message_id="msg-old",
        occurred_at=now - timedelta(hours=73),
        example="有儿童用品吗",
    )
    await candidates.mark_draft_pending(candidate.id)
    pending = await candidates.get(candidate.id)
    assert pending is not None
    assert pending.draft_status is KnowledgeCandidateDraftStatus.PENDING
    assert pending.draft_generation == 1

    await candidates.mark_draft_failed(candidate.id)
    failed = await candidates.get(candidate.id)
    assert failed is not None
    assert failed.draft_status is KnowledgeCandidateDraftStatus.FAILED

    knowledge = KnowledgeEntry(
        category="设施",
        question_zh="是否提供儿童用品？",
        answer_zh="请联系管理员确认。",
        question_en="Are children's supplies available?",
        answer_en="Please confirm with the host.",
        keywords=["儿童用品"],
    )
    session.add(knowledge)
    await session.flush()
    await candidates.convert(candidate.id, knowledge_entry_id=knowledge.id)
    removed = await candidates.prune_occurrences(before=now - timedelta(hours=72))
    await session.commit()
    converted = await candidates.get(candidate.id)

    assert removed == 0
    assert converted is not None
    assert converted.status is KnowledgeCandidateStatus.CONVERTED
    assert converted.knowledge_entry_id == knowledge.id
    assert converted.examples == []
    assert converted.draft_payload is None
    assert await candidates.list_context(now=now) == []
