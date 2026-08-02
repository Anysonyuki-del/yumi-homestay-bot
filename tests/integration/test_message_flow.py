from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import (
    _handle_guest_delivery_failure,
    _notify_guest_delivery_failure,
)
from homestay_bot.domain.enums import BusinessTaskStatus, BusinessTaskType, MessageOrigin
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    BusinessTask,
    Conversation,
    Customer,
    Job,
    Message,
)
from homestay_bot.repositories.context import SQLAlchemyContextRepository
from homestay_bot.repositories.conversations import (
    SQLAlchemyConversationRepository,
    SQLAlchemyMessageRepository,
)
from homestay_bot.services.message_service import IncomingMessage, MessageService


@pytest.mark.asyncio
async def test_message_flow_creates_conversation_and_deduplicates_message() -> None:
    """真实仓储应创建唯一会话，并让同一企业微信消息只入库一次。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    incoming = IncomingMessage(
        msgid="msg-1",
        open_kfid="wk-1",
        external_userid="wm-1",
        origin=MessageOrigin.GUEST,
        msgtype="text",
        content="你好",
        sent_at=datetime(2026, 7, 29, tzinfo=UTC),
        metadata={"media_id": "safe-media-id"},
    )

    async with factory() as session:
        conversations = SQLAlchemyConversationRepository(session)
        messages = MessageService(SQLAlchemyMessageRepository(session))
        conversation = await conversations.get_or_create(incoming)
        first = await messages.record_incoming(conversation.id, incoming)
        await session.commit()

        second = await messages.record_incoming(conversation.id, incoming)
        await session.commit()
        context = await messages.build_context(conversation.id)

        assert first is True
        assert second is False
        stored = await session.get(Message, 1)
        assert stored is not None
        assert stored.message_metadata == {"media_id": "safe-media-id"}
        assert context == [{"role": "user", "content": "你好"}]

        await messages.record_bot(
            conversation.id,
            "outbox:temporary",
            "您好",
            sent_at=incoming.sent_at,
        )
        await SQLAlchemyMessageRepository(
            session
        ).replace_external_message_id(
            "outbox:temporary",
            "wecom-real-msgid",
        )
        await session.commit()

        assert (
            await SQLAlchemyMessageRepository(session).exists(
                "wecom-real-msgid"
            )
            is True
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_unique_race_preserves_outer_transaction(monkeypatch) -> None:
    """会话唯一键竞争应返回已有会话，且外层业务写入仍可提交。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    incoming = IncomingMessage(
        msgid="conversation-race-message",
        open_kfid="wk-race",
        external_userid="wm-race",
        origin=MessageOrigin.GUEST,
        msgtype="text",
        content="并发消息",
        sent_at=datetime(2026, 8, 3, tzinfo=UTC),
    )

    async with factory() as session:
        existing = Conversation(open_kfid="wk-race", external_userid="wm-race")
        session.add(existing)
        await session.commit()
        existing_id = existing.id

    async with factory() as session:
        session.add(
            AuditLog(
                actor_employee_id=None,
                action="conversation_outer_marker",
                target_type="test",
                target_id="conversation-race",
                details={},
            )
        )
        original_scalar = session.scalar
        scalar_calls = 0

        async def scalar_after_race(statement, *args, **kwargs):
            """第一次查询模拟未命中，冲突后读取竞争方已提交的会话。"""
            nonlocal scalar_calls
            scalar_calls += 1
            if scalar_calls == 1:
                return None
            return await original_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", scalar_after_race)
        conversation = await SQLAlchemyConversationRepository(session).get_or_create(
            incoming
        )
        await session.commit()

        assert conversation.id == existing_id
        assert await session.scalar(
            select(AuditLog.id).where(
                AuditLog.action == "conversation_outer_marker"
            )
        ) is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_message_unique_race_preserves_outer_transaction() -> None:
    """消息唯一键竞争应返回 False，且外层业务写入仍可提交。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sent_at = datetime(2026, 8, 3, tzinfo=UTC)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-message", external_userid="wm-message")
        session.add(conversation)
        await session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                external_message_id="message-race",
                origin=MessageOrigin.GUEST,
                message_type="text",
                content="竞争方消息",
                message_metadata={},
                sent_at=sent_at,
            )
        )
        await session.commit()
        conversation_id = conversation.id

    async with factory() as session:
        session.add(
            AuditLog(
                actor_employee_id=None,
                action="message_outer_marker",
                target_type="test",
                target_id="message-race",
                details={},
            )
        )
        added = await SQLAlchemyMessageRepository(session).add(
            Message(
                conversation_id=conversation_id,
                external_message_id="message-race",
                origin=MessageOrigin.GUEST,
                message_type="text",
                content="本 worker 消息",
                message_metadata={},
                sent_at=sent_at,
            )
        )
        await session.commit()

        assert added is False
        assert await session.scalar(
            select(AuditLog.id).where(AuditLog.action == "message_outer_marker")
        ) is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_message_savepoint_does_not_swallow_outer_integrity_error() -> None:
    """消息保存点不得把外层业务约束错误误判成重复消息。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sent_at = datetime(2026, 8, 3, tzinfo=UTC)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-invalid", external_userid="wm-invalid")
        session.add(conversation)
        await session.commit()
        conversation_id = conversation.id

    async with factory() as session:
        session.add(
            BusinessTask(
                source_message_id="invalid-message-outer-task",
                task_type=BusinessTaskType.SUPPLIES,
                status=BusinessTaskStatus.PENDING_ASSIGNMENT,
                property_id=None,
                service_date=None,
                description="缺少执行字段",
            )
        )

        with pytest.raises(IntegrityError):
            await SQLAlchemyMessageRepository(session).add(
                Message(
                    conversation_id=conversation_id,
                    external_message_id="new-message-after-invalid-task",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="新消息",
                    message_metadata={},
                    sent_at=sent_at,
                )
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_message_savepoint_does_not_swallow_foreign_key_error() -> None:
    """消息自身的外键错误必须上抛，不能被误判成重复消息。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        with pytest.raises(IntegrityError):
            await SQLAlchemyMessageRepository(session).add(
                Message(
                    conversation_id=999,
                    external_message_id="message-invalid-foreign-key",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="不存在的会话",
                    message_metadata={},
                    sent_at=datetime(2026, 8, 3, tzinfo=UTC),
                )
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_bot_delivery_failure_is_recorded_without_polluting_context(
    ) -> None:
    """企业微信异步失败回执应标记机器人消息，且失败正文不再进入模型上下文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        messages = MessageService(SQLAlchemyMessageRepository(session))
        await messages.record_bot(
            conversation.id,
            "wecom-msg-1",
            "这条消息没有真正送达",
            sent_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        repository = SQLAlchemyMessageRepository(session)
        failed = await repository.mark_delivery_failed(
            "wecom-msg-1",
            error_code="wecom_async_13",
        )
        await session.commit()

        assert failed is not None
        assert failed.message_metadata == {
            "delivery_status": "failed",
            "delivery_error_code": "wecom_async_13",
            "delivery_retry_count": 0,
        }
        assert await messages.build_context(conversation.id) == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_async_guest_delivery_failure_queues_one_safe_retry() -> None:
    """异步失败回执应为普通机器人回复创建一次去重重试任务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        messages = MessageService(SQLAlchemyMessageRepository(session))
        await messages.record_bot(conversation.id, "wecom-msg-2", "请稍等，我马上为您核实。")

        assert await _handle_guest_delivery_failure(
            session,
            "wecom-msg-2",
            fail_type=10,
        ) is True
        assert await _handle_guest_delivery_failure(
            session,
            "wecom-msg-2",
            fail_type=10,
        ) is False
        jobs = list((await session.scalars(select(Job))).all())

        assert len(jobs) == 1
        assert jobs[0].job_type == "wecom_send_text"
        assert jobs[0].payload["delivery_retry_count"] == 1
        assert jobs[0].payload["retry_of_message_id"] == str(1)
        assert jobs[0].payload["content"] == "请稍等，我马上为您核实。"

    await engine.dispose()


@pytest.mark.asyncio
async def test_security_restricted_guest_delivery_uses_safe_fallback() -> None:
    """安全限制失败不得重复发送被拦截正文，而应改发短兜底。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    blocked_content = "关于路线的详细说明，含有平台可能拦截的内容。"
    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        messages = MessageService(SQLAlchemyMessageRepository(session))
        await messages.record_bot(
            conversation.id,
            "wecom-msg-security",
            blocked_content,
        )

        assert await _handle_guest_delivery_failure(
            session,
            "wecom-msg-security",
            fail_type=13,
        ) is True
        jobs = list((await session.scalars(select(Job))).all())

        assert len(jobs) == 1
        assert jobs[0].payload["content"] != blocked_content
        assert jobs[0].payload["content"] == (
            "我已收到您的问题，正在为您核实相关信息，请稍等片刻。"
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_exhausted_guest_delivery_failure_notifies_staff_once() -> None:
    """重试仍失败时应只登记一次人工跟进通知。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        messages = MessageService(SQLAlchemyMessageRepository(session))
        await messages.record_bot(
            conversation.id,
            "wecom-msg-3",
            "这条重试消息仍未送达",
            metadata={
                "delivery_status": "failed",
                "delivery_retry_count": 1,
                "delivery_retry_pending": False,
            },
        )
        repository = SQLAlchemyMessageRepository(session)
        await repository.mark_delivery_failed(
            "wecom-msg-3",
            error_code="wecom_async_13",
        )

        assert await _notify_guest_delivery_failure(
            session,
            "wecom-msg-3",
            agent_id=1000002,
            employee_userids=["staff-1"],
        ) is True
        assert await _notify_guest_delivery_failure(
            session,
            "wecom-msg-3",
            agent_id=1000002,
            employee_userids=["staff-1"],
        ) is False
        jobs = list((await session.scalars(select(Job))).all())

        assert len(jobs) == 1
        assert jobs[0].job_type == "wecom_send_internal_text"

    await engine.dispose()


@pytest.mark.asyncio
async def test_context_candidates_are_isolated_and_keep_three_recent_raw_messages() -> None:
    """摘要候选必须按客户隔离，并排除每位客户最近三条原文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 7, 31, 8, tzinfo=UTC)

    async with factory() as session:
        first_customer = Customer(display_name="客户一")
        second_customer = Customer(display_name="客户二")
        first_conversation = Conversation(
            customer=first_customer,
            open_kfid="wk-1",
            external_userid="wm-1",
        )
        second_conversation = Conversation(
            customer=second_customer,
            open_kfid="wk-1",
            external_userid="wm-2",
        )
        session.add_all([first_conversation, second_conversation])
        await session.flush()
        for index in range(5):
            session.add(
                Message(
                    conversation_id=first_conversation.id,
                    external_message_id=f"first-{index}",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content=f"客户一消息{index}",
                    sent_at=now,
                )
            )
        session.add(
            Message(
                conversation_id=second_conversation.id,
                external_message_id="second-1",
                origin=MessageOrigin.GUEST,
                message_type="text",
                content="客户二私有消息",
                sent_at=now,
            )
        )
        await session.commit()

        candidates = await SQLAlchemyContextRepository(
            session
        ).list_short_candidates(first_customer.id, now, raw_limit=3)

        assert [item.content for item in candidates] == [
            "客户一消息0",
            "客户一消息1",
        ]
        assert all("客户二" not in (item.content or "") for item in candidates)

    await engine.dispose()


@pytest.mark.asyncio
async def test_context_follows_processing_order_when_timestamps_use_different_zones() -> None:
    """混合时区时间戳不得把旧机器人回复排到最新客人问题之后。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = IncomingMessage(
        msgid="guest-1",
        open_kfid="wk-1",
        external_userid="wm-1",
        origin=MessageOrigin.GUEST,
        msgtype="text",
        content="今天入住明天退房",
        sent_at=datetime(2026, 7, 29, 4, 0, tzinfo=UTC),
    )
    second = IncomingMessage(
        msgid="guest-2",
        open_kfid="wk-1",
        external_userid="wm-1",
        origin=MessageOrigin.GUEST,
        msgtype="text",
        content="怎样和朋友协调旅行安排？",
        sent_at=datetime(2026, 7, 29, 4, 1, tzinfo=UTC),
    )

    async with factory() as session:
        conversations = SQLAlchemyConversationRepository(session)
        repository = SQLAlchemyMessageRepository(session)
        messages = MessageService(repository)
        conversation = await conversations.get_or_create(first)
        await messages.record_incoming(conversation.id, first)
        await messages.record_bot(
            conversation.id,
            "bot-1",
            "请提供入住日期。",
            # 模拟旧代码把武汉本地时间当成可与 UTC 直接排序的时间。
            sent_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
        )
        await messages.record_incoming(conversation.id, second)
        await session.commit()

        assert await messages.build_context(conversation.id) == [
            {"role": "user", "content": "今天入住明天退房"},
            {"role": "assistant", "content": "请提供入住日期。"},
            {"role": "user", "content": "怎样和朋友协调旅行安排？"},
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_context_is_bounded_by_source_message_and_ack_does_not_consume_limit() -> None:
    """最终模型只能读取来源消息及之前的正式文本，安抚不占上下文条数。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 1, tzinfo=UTC)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    external_message_id="guest-1",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="今天入住明天退房",
                    sent_at=now,
                ),
                Message(
                    conversation_id=conversation.id,
                    external_message_id="ack-1",
                    origin=MessageOrigin.BOT,
                    message_type="ack",
                    content="收到啦，我来帮您看看。",
                    sent_at=now,
                ),
                Message(
                    conversation_id=conversation.id,
                    external_message_id="guest-2",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="可以补两瓶矿泉水吗？",
                    sent_at=now,
                ),
            ]
        )
        await session.commit()
        messages = MessageService(SQLAlchemyMessageRepository(session))

        context = await messages.build_context(
            conversation.id,
            limit=3,
            through_external_message_id="guest-1",
        )

        assert context == [
            {"role": "user", "content": "今天入住明天退房"}
        ]

    await engine.dispose()
