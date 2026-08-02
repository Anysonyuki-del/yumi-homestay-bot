from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import BusinessTaskStatus, BusinessTaskType, MessageOrigin
from homestay_bot.domain.models import (
    Base,
    BusinessTask,
    Conversation,
    Customer,
    Message,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.repositories.context import SQLAlchemyContextRepository


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
            self.batch_sizes.append(len(messages))
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
