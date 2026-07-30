import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    KnowledgeCandidateDraftStatus,
    KnowledgeCandidateStatus,
)
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    KnowledgeCandidate,
    KnowledgeCandidateOccurrence,
    KnowledgeEntry,
)
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
    """来源消息只能计数一次，窗口统计含边界且保留最近三条示例。"""
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
        "参考问法 3",
        "参考问法 4",
        "参考问法 5",
    ]
    assert refreshed.examples_version == 5


@pytest.mark.asyncio
async def test_occurrences_compare_utc_times_across_sqlite_sessions() -> None:
    """SQLite 重读无时区时间后，后续 UTC 消息仍应正常更新最近出现时间。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)

    async with factory() as session:
        candidate = await SQLAlchemyFaqCandidateRepository(
            session
        ).get_or_create(
            canonical_question="民宿是否提供停车位？",
            category="交通",
        )
        candidate_id = candidate.id
        await SQLAlchemyFaqCandidateRepository(session).add_occurrence(
            candidate_id,
            source_message_id="msg-first-session",
            occurred_at=now,
            example="有停车位吗？",
        )
        await session.commit()

    async with factory() as session:
        repository = SQLAlchemyFaqCandidateRepository(session)
        added = await repository.add_occurrence(
            candidate_id,
            source_message_id="msg-second-session",
            occurred_at=now + timedelta(minutes=1),
            example="停车方便吗？",
        )
        await session.commit()
        refreshed = await repository.get(candidate_id)

    assert added is True
    assert refreshed is not None
    assert refreshed.total_occurrences == 2
    await engine.dispose()


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
    assert refreshed.total_occurrences == 0
    assert refreshed.last_threshold_total == 0
    assert refreshed.last_reminded_total == 0
    assert refreshed.last_reminded_at is None
    assert await candidates.count_since(
        candidate.id,
        since=now - timedelta(days=1),
        until=now + timedelta(days=31),
    ) == 0
    audit = await session.scalar(
        select(AuditLog).where(
            AuditLog.action == "faq_candidate.reopen",
            AuditLog.target_id == str(candidate.id),
        )
    )
    assert audit is not None
    assert audit.actor_employee_id is None
    assert audit.details == {"candidate_id": candidate.id}


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
    assert pending.draft_attempts == 0

    first_failure = await candidates.increment_draft_attempts(candidate.id)
    second_failure = await candidates.increment_draft_attempts(candidate.id)
    assert first_failure.draft_attempts == 2
    assert second_failure.draft_attempts == 2

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
    stale_result = await candidates.mark_draft_ready(
        candidate.id,
        {"answer_zh": "不应恢复的旧草稿"},
        expected_generation=1,
    )
    removed = await candidates.prune_occurrences(before=now - timedelta(hours=72))
    await session.commit()
    converted = await candidates.get(candidate.id)

    assert removed == 0
    assert converted is not None
    assert converted.status is KnowledgeCandidateStatus.CONVERTED
    assert converted.draft_generation == 2
    assert stale_result is None
    assert converted.knowledge_entry_id == knowledge.id
    assert converted.examples == []
    assert converted.draft_payload is None
    assert await candidates.list_context(now=now) == []


@pytest.mark.asyncio
async def test_notification_advances_threshold_to_delivery_time_total(repository) -> None:
    """冷却等待期间积压的次数应在实际通知时全部纳入阈值游标。"""
    candidates, session = repository
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)
    candidate = await candidates.get_or_create(
        canonical_question="是否提供儿童用品？",
        category="设施",
    )
    for index in range(1, 9):
        await candidates.add_occurrence(
            candidate.id,
            source_message_id=f"queued-{index}",
            occurred_at=now,
            example="是否提供儿童用品？",
        )
    candidate.last_threshold_total = 6
    # 模拟 DeepSeek 调用期间其他事务已把数据库累计推进，但当前 identity map 仍为旧值。
    await session.execute(
        update(KnowledgeCandidate)
        .where(KnowledgeCandidate.id == candidate.id)
        .values(total_occurrences=11)
        .execution_options(synchronize_session=False)
    )
    assert candidate.total_occurrences == 8

    notified = await candidates.mark_notified(
        candidate.id,
        reminded_at=now + timedelta(hours=24),
    )
    await session.commit()

    assert notified.total_occurrences == 11
    assert notified.last_reminded_total == 11
    assert notified.last_threshold_total == 11


@pytest.mark.asyncio
async def test_maintenance_prunes_old_details_without_new_question(repository) -> None:
    """周期维护应主动清除窗口外明细，不依赖下一条合格 FAQ。"""
    candidates, session = repository
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)
    candidate = await candidates.get_or_create(
        canonical_question="是否提供儿童用品？",
        category="设施",
    )
    await candidates.add_occurrence(
        candidate.id,
        source_message_id="old-without-followup",
        occurred_at=now - timedelta(hours=73),
        example="是否提供儿童用品？",
    )

    removed, reopened = await candidates.maintain(now=now)
    await session.commit()

    assert removed == 1
    assert reopened == 0
    assert await candidates.count_since(
        candidate.id,
        since=now - timedelta(days=365),
        until=now,
    ) == 0


@pytest.mark.asyncio
async def test_concurrent_candidate_and_occurrence_writes_remain_consistent(
    tmp_path,
) -> None:
    """并发创建与消息去重后只能有一个候选，累计数必须等于明细数。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'faq-race.db'}?timeout=30"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_candidate() -> int:
        """在独立事务并发创建同一标准问题。"""
        async with factory() as session:
            candidate = await SQLAlchemyFaqCandidateRepository(
                session
            ).get_or_create(
                canonical_question="民宿是否提供停车位？",
                category="交通",
            )
            await session.commit()
            return candidate.id

    first_id, second_id = await asyncio.gather(
        create_candidate(),
        create_candidate(),
    )

    async def add_message(message_id: str) -> bool:
        """在独立事务并发写入同一候选的消息。"""
        async with factory() as session:
            added = await SQLAlchemyFaqCandidateRepository(
                session
            ).add_occurrence(
                first_id,
                source_message_id=message_id,
                occurred_at=datetime(2026, 7, 30, tzinfo=UTC),
                example=message_id,
            )
            await session.commit()
            return added

    results = await asyncio.gather(
        add_message("same-message"),
        add_message("same-message"),
        add_message("different-message"),
    )

    async with factory() as session:
        candidate_count = await session.scalar(
            select(func.count(KnowledgeCandidate.id))
        )
        occurrence_count = await session.scalar(
            select(func.count(KnowledgeCandidateOccurrence.id))
        )
        candidate = await session.get(KnowledgeCandidate, first_id)

    assert first_id == second_id
    assert candidate_count == 1
    assert sorted(results) == [False, True, True]
    assert occurrence_count == 2
    assert candidate is not None
    assert candidate.total_occurrences == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_reopen_is_claimed_once_without_duplicate_audit(
    tmp_path,
) -> None:
    """维护与模型上下文并发重开时，只能有一个事务取得重开权。"""
    database_url = (
        f"sqlite+aiosqlite:///{tmp_path / 'faq-reopen-race.db'}?timeout=30"
    )
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 7, 30, tzinfo=UTC)

    async with factory() as session:
        repository = SQLAlchemyFaqCandidateRepository(session)
        candidate = await repository.get_or_create(
            canonical_question="能否寄存行李？",
            category="服务",
        )
        candidate_id = candidate.id
        await repository.snooze(
            candidate_id,
            until=now,
        )
        await session.commit()

    async def reopen() -> int:
        """在独立事务竞争同一个到期候选。"""
        async with factory() as session:
            count = await SQLAlchemyFaqCandidateRepository(
                session
            ).reopen_expired(now=now)
            await session.commit()
            return count

    results = await asyncio.gather(reopen(), reopen())

    async with factory() as session:
        audit_count = await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "faq_candidate.reopen",
                AuditLog.target_id == str(candidate_id),
            )
        )
        reopened = await session.get(KnowledgeCandidate, candidate_id)

    assert sorted(results) == [0, 1]
    assert audit_count == 1
    assert reopened is not None
    assert reopened.status is KnowledgeCandidateStatus.OPEN
    assert reopened.total_occurrences == 0
    await engine.dispose()
