from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    CustomerMemoryCategory,
    CustomerMemoryEvidenceType,
    CustomerMemoryStatus,
    MessageOrigin,
)
from homestay_bot.domain.models import (
    Base,
    BusinessTask,
    Conversation,
    Customer,
    CustomerMemoryItem,
    Message,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.repositories.context import SQLAlchemyContextRepository
from homestay_bot.services.context_retention import (
    ContextRetentionService,
    ContextSummaryResult,
    CustomerMemoryCandidate,
)


async def _customer_message(
    session,
    *,
    customer_name: str,
    external_message_id: str,
    content: str,
) -> tuple[Customer, Message]:
    """建立可被结构化记忆引用的正式客户消息。"""
    customer = Customer(display_name=customer_name)
    session.add(customer)
    await session.flush()
    conversation = Conversation(
        customer_id=customer.id,
        open_kfid=f"kfid-{external_message_id}",
        external_userid=f"user-{external_message_id}",
    )
    session.add(conversation)
    await session.flush()
    source = Message(
        conversation_id=conversation.id,
        external_message_id=external_message_id,
        origin=MessageOrigin.GUEST,
        message_type="text",
        content=content,
        sent_at=datetime.now(UTC),
    )
    session.add(source)
    await session.flush()
    return customer, source


@pytest.mark.asyncio
async def test_explicit_memory_is_recalled_only_for_owner_and_relevant_query() -> None:
    """客户明示稳定事实只对同一客户和相关问题进入模型上下文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        customer, source = await _customer_message(
            session,
            customer_name="查理主人",
            external_message_id="memory-dog",
            content="我的狗叫查理",
        )
        other = Customer(display_name="另一位客户")
        session.add(other)
        await session.flush()
        repository = SQLAlchemyContextRepository(session)
        await repository.save_short_summary(
            customer.id,
            ContextSummaryResult(
                summary="客户养狗",
                unresolved_items=[],
                memory_candidates=[
                    CustomerMemoryCandidate(
                        subject_key="pet_dog_name",
                        category=CustomerMemoryCategory.CONFIRMED_FACT,
                        statement="客户的狗叫查理",
                        evidence_type=CustomerMemoryEvidenceType.USER_EXPLICIT,
                        source_message_id=source.external_message_id,
                        confidence=0.98,
                    )
                ],
            ),
            [source],
            now,
        )

        owner_context = await repository.load_model_context(
            customer.id, query="我的狗叫什么？"
        )
        unrelated_context = await repository.load_model_context(
            customer.id, query="几点退房？"
        )
        other_context = await repository.load_model_context(
            other.id, query="我的狗叫什么？"
        )

        assert owner_context.memories == [
            {
                "subject_key": "pet_dog_name",
                "category": "confirmed_fact",
                "statement": "客户的狗叫查理",
                "confidence": 0.98,
            }
        ]
        assert unrelated_context.memories == []
        assert other_context.memories == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_single_recent_message_becomes_cross_conversation_memory() -> None:
    """最近原文无需等待退出三条窗口，也能在维护后供新会话召回。"""

    class RecentMemorySummarizer:
        """从单条最近原文返回确定的明示记忆。"""

        async def summarize(self, *, tier, existing_summary, messages):
            """验证最近消息只参与观察，不进入短摘要。"""
            assert tier == "memory"
            assert messages[0].summary_eligible is False
            return ContextSummaryResult(
                summary="无新增摘要",
                unresolved_items=[],
                memory_candidates=[
                    CustomerMemoryCandidate(
                        "pet_dog_name",
                        CustomerMemoryCategory.CONFIRMED_FACT,
                        "客户的狗叫查理",
                        CustomerMemoryEvidenceType.USER_EXPLICIT,
                        messages[0].message_id,
                        0.99,
                    )
                ],
            )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        customer, source = await _customer_message(
            session,
            customer_name="单消息客户",
            external_message_id="single-recent-dog",
            content="我的狗叫查理",
        )
        repository = SQLAlchemyContextRepository(session)

        await ContextRetentionService(
            repository,
            RecentMemorySummarizer(),
        ).maintain_customer(customer.id, now)
        context = await repository.load_model_context(
            customer.id, query="我的狗叫什么？"
        )

        assert source.content == "我的狗叫查理"
        assert source.short_summarized_at is None
        assert source.memory_processed_at == now
        assert context.memories[0]["statement"] == "客户的狗叫查理"

    await engine.dispose()


@pytest.mark.asyncio
async def test_model_inference_stays_candidate_and_explicit_correction_supersedes() -> None:
    """模型推断不得召回，客户明确纠正同主题时应覆盖旧有效记忆。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        customer, first = await _customer_message(
            session,
            customer_name="偏好客户",
            external_message_id="memory-first",
            content="我喜欢高楼层",
        )
        conversation = await session.get(Conversation, first.conversation_id)
        correction = Message(
            conversation_id=conversation.id,
            external_message_id="memory-correction",
            origin=MessageOrigin.GUEST,
            message_type="text",
            content="更正一下，我喜欢低楼层",
            sent_at=now + timedelta(minutes=1),
        )
        session.add(correction)
        await session.flush()
        repository = SQLAlchemyContextRepository(session)
        inferred = CustomerMemoryCandidate(
            subject_key="drink_preference",
            category=CustomerMemoryCategory.PREFERENCE,
            statement="客户可能喜欢茶",
            evidence_type=CustomerMemoryEvidenceType.MODEL_INFERENCE,
            source_message_id=first.external_message_id,
            confidence=0.95,
        )
        await repository.save_short_summary(
            customer.id,
            ContextSummaryResult("偏好", [], [inferred]),
            [first],
            now,
        )
        await repository.save_short_summary(
            customer.id,
            ContextSummaryResult(
                "偏好高楼层",
                [],
                [
                    CustomerMemoryCandidate(
                        "floor_preference",
                        CustomerMemoryCategory.PREFERENCE,
                        "客户喜欢高楼层",
                        CustomerMemoryEvidenceType.USER_EXPLICIT,
                        first.external_message_id,
                        0.95,
                    )
                ],
            ),
            [first],
            now,
        )
        await repository.save_short_summary(
            customer.id,
            ContextSummaryResult(
                "偏好低楼层",
                [],
                [
                    CustomerMemoryCandidate(
                        "floor_preference",
                        CustomerMemoryCategory.PREFERENCE,
                        "客户喜欢低楼层",
                        CustomerMemoryEvidenceType.USER_EXPLICIT,
                        correction.external_message_id,
                        0.99,
                        is_correction=True,
                    )
                ],
            ),
            [correction],
            now + timedelta(minutes=1),
        )

        memories = list(
            (
                await session.scalars(
                    select(CustomerMemoryItem).order_by(CustomerMemoryItem.id)
                )
            ).all()
        )
        context = await repository.load_model_context(
            customer.id, query="我喜欢什么楼层？"
        )

        assert memories[0].status is CustomerMemoryStatus.CANDIDATE
        assert memories[1].status is CustomerMemoryStatus.SUPERSEDED
        assert memories[2].status is CustomerMemoryStatus.ACTIVE
        assert context.memories[0]["statement"] == "客户喜欢低楼层"

    await engine.dispose()


@pytest.mark.asyncio
async def test_unresolved_items_merge_and_active_memory_expires_to_stale() -> None:
    """摘要层级不得覆盖待确认项，到复核期的记忆必须停止召回。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        customer, source = await _customer_message(
            session,
            customer_name="待确认客户",
            external_message_id="memory-expiry",
            content="我喜欢安静",
        )
        repository = SQLAlchemyContextRepository(session)
        candidate = CustomerMemoryCandidate(
            "quiet_preference",
            CustomerMemoryCategory.PREFERENCE,
            "客户喜欢安静",
            CustomerMemoryEvidenceType.USER_EXPLICIT,
            source.external_message_id,
            0.9,
        )
        await repository.save_short_summary(
            customer.id,
            ContextSummaryResult("短摘要", ["待确认到达时间"], [candidate]),
            [source],
            now,
        )
        await repository.save_long_summary_and_purge(
            customer.id,
            ContextSummaryResult("长摘要", ["待确认开票抬头"]),
            [source],
            now,
        )
        await repository.expire_customer_memories(
            customer.id, now + timedelta(days=366)
        )
        summary = await repository.get_summary(customer.id)
        memory = await session.scalar(select(CustomerMemoryItem))

        assert summary.unresolved_items == ["待确认到达时间", "待确认开票抬头"]
        assert memory.status is CustomerMemoryStatus.STALE
        assert (
            await repository.load_model_context(customer.id, query="我喜欢什么环境？")
        ).memories == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_model_context_includes_safe_active_orders_and_open_tasks() -> None:
    """模型上下文只加入订单和任务的运营摘要，不复制任务正文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        customer = Customer(display_name="测试客户")
        property_profile = PropertyProfile(id=101, title="长江中心")
        session.add_all([customer, property_profile])
        await session.flush()
        session.add(
            StayOrder(
                hostex_reservation_code="R-CONTEXT",
                stay_code="S-CONTEXT",
                customer_id=customer.id,
                property_id=101,
                check_in_date=date(2026, 8, 1),
                check_out_date=date(2026, 8, 2),
                status="confirmed",
            )
        )
        session.add(
            BusinessTask(
                source_message_id="msg-context",
                task_type=BusinessTaskType.SUPPLIES,
                status=BusinessTaskStatus.PENDING_CONFIRMATION,
                customer_id=customer.id,
                description="补水，联系电话13800138000",
            )
        )
        await session.commit()

        context = await SQLAlchemyContextRepository(session).load_model_context(
            customer.id
        )

        assert context.active_orders == [
            {
                "property_id": 101,
                "property_title": "长江中心",
                "check_in_date": "2026-08-01",
                "check_out_date": "2026-08-02",
                "status": "confirmed",
            }
        ]
        assert context.open_tasks == [
            {
                "task_type": "supplies",
                "status": "pending_confirmation",
                "property_id": None,
                "service_date": None,
            }
        ]
        assert "13800138000" not in str(context)
        assert "补水" not in str(context)

    await engine.dispose()


@pytest.mark.asyncio
async def test_customer_room_number_requires_one_active_order() -> None:
    """房间号只在客户恰好一笔有效订单时返回。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        customer = Customer(display_name="房间号客户")
        first_property = PropertyProfile(
            id=201,
            title="房间一",
            room_number="1201",
        )
        second_property = PropertyProfile(id=202, title="房间二")
        session.add_all([customer, first_property, second_property])
        await session.flush()
        session.add(
            StayOrder(
                hostex_reservation_code="R-ROOM-1",
                stay_code="S-ROOM-1",
                customer_id=customer.id,
                property_id=201,
                check_in_date=date(2026, 8, 1),
                check_out_date=date(2026, 8, 2),
                status="confirmed",
            )
        )
        await session.commit()
        repository = SQLAlchemyContextRepository(session)
        assert await repository.get_customer_room_number(customer.id) == "1201"

        session.add(
            StayOrder(
                hostex_reservation_code="R-ROOM-2",
                stay_code="S-ROOM-2",
                customer_id=customer.id,
                property_id=202,
                check_in_date=date(2026, 8, 3),
                check_out_date=date(2026, 8, 4),
                status="confirmed",
            )
        )
        await session.commit()
        assert await repository.get_customer_room_number(customer.id) is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_context_summary_candidates_are_batched() -> None:
    """上下文维护一次只读取固定批量，避免高频客户造成无界内存和模型输入。"""
    from datetime import UTC, datetime, timedelta

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        customer = Customer(display_name="批量客户")
        session.add(customer)
        await session.flush()
        conversation = Conversation(
            customer_id=customer.id,
            open_kfid="batch-kfid",
            external_userid="batch-user",
        )
        session.add(conversation)
        await session.flush()
        now = datetime(2026, 8, 1, tzinfo=UTC)
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    external_message_id=f"batch-{index}",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content=f"历史消息 {index}",
                    sent_at=now - timedelta(hours=index + 1),
                )
                for index in range(60)
            ]
        )
        await session.flush()

        candidates = await SQLAlchemyContextRepository(session).list_short_candidates(
            customer.id,
            now,
            raw_limit=3,
        )

        assert len(candidates) == 50
        assert candidates[0].content == "历史消息 7"
        assert candidates[-1].content == "历史消息 56"

    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_context_messages_are_batched() -> None:
    """七天外原文也必须分批读取，避免一次摘要加载无界数据。"""
    from datetime import UTC, datetime, timedelta

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        customer = Customer(display_name="过期批量客户")
        session.add(customer)
        await session.flush()
        conversation = Conversation(
            customer_id=customer.id,
            open_kfid="expired-kfid",
            external_userid="expired-user",
        )
        session.add(conversation)
        await session.flush()
        now = datetime(2026, 8, 1, tzinfo=UTC)
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    external_message_id=f"expired-{index}",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content=f"过期消息 {index}",
                    sent_at=now - timedelta(days=8, minutes=index),
                )
                for index in range(60)
            ]
        )
        await session.flush()

        expired = await SQLAlchemyContextRepository(session).list_expired_unpurged(
            customer.id,
            now - timedelta(days=7),
        )

        assert len(expired) == 50
        assert expired[0].content == "过期消息 0"
        assert expired[-1].content == "过期消息 49"

    await engine.dispose()


@pytest.mark.asyncio
async def test_context_summary_multiple_batches_eventually_cover_all_candidates() -> None:
    """连续维护应处理所有候选，只保留配置要求的最近三条原文。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from homestay_bot.domain.enums import MessageOrigin
    from homestay_bot.domain.models import Base, Conversation, Customer, Message
    from homestay_bot.services.context_retention import (
        ContextRetentionService,
        ContextSummaryResult,
    )

    class SummarizerStub:
        """为每批返回确定摘要并记录批量大小。"""

        def __init__(self) -> None:
            """初始化批次记录。"""
            self.batch_sizes: list[int] = []

        async def summarize(self, *, tier, existing_summary, messages):
            """返回不含敏感信息的固定合并结果。"""
            assert tier == "short"
            self.batch_sizes.append(
                sum(1 for item in messages if item.summary_eligible)
            )
            return ContextSummaryResult(
                summary=f"已处理 {sum(self.batch_sizes)} 条",
                unresolved_items=[],
            )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime(2026, 8, 3, tzinfo=UTC)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        customer = Customer(display_name="多批客户")
        session.add(customer)
        await session.flush()
        conversation = Conversation(
            customer_id=customer.id,
            open_kfid="multi-batch-kfid",
            external_userid="multi-batch-user",
        )
        session.add(conversation)
        await session.flush()
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    external_message_id=f"multi-batch-{index}",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content=f"候选消息 {index}",
                    sent_at=now - timedelta(hours=1),
                )
                for index in range(120)
            ]
        )
        await session.flush()
        summarizer = SummarizerStub()
        service = ContextRetentionService(
            SQLAlchemyContextRepository(session),
            summarizer,
            raw_limit=3,
        )

        for _ in range(3):
            await service.maintain_customer(customer.id, now)

        summarized_count = await session.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation.id,
                Message.short_summarized_at.is_not(None),
            )
        )
        remaining = list(
            (
                await session.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation.id,
                        Message.short_summarized_at.is_(None),
                    )
                    .order_by(Message.id)
                )
            ).all()
        )

        assert summarizer.batch_sizes == [50, 50, 17]
        assert summarized_count == 117
        assert [item.content for item in remaining] == [
            "候选消息 117",
            "候选消息 118",
            "候选消息 119",
        ]

    await engine.dispose()
