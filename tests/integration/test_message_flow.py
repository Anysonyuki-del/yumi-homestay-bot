from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import (
    TransactionalOutboxWeCom,
    _compensate_guest_delivery_failure,
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
from homestay_bot.services.delivery_rewrite_job import GuestDeliveryRewriteJobService
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
        ) is True
        jobs = list((await session.scalars(select(Job))).all())

        assert len(jobs) == 1
        assert jobs[0].job_type == "wecom_send_text"
        assert jobs[0].payload["delivery_retry_count"] == 1
        assert jobs[0].payload["retry_of_message_id"] == str(1)
        assert jobs[0].payload["content"] == "请稍等，我马上为您核实。"

    await engine.dispose()


@pytest.mark.asyncio
async def test_security_restricted_guest_delivery_queues_one_rewrite_job() -> None:
    """首次安全限制只登记一次改写任务，且任务载荷不得复制对话正文。"""
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
        assert await _handle_guest_delivery_failure(
            session,
            "wecom-msg-security",
            fail_type=13,
        ) is False
        failed_message = await session.scalar(
            select(Message).where(
                Message.external_message_id == "wecom-msg-security"
            )
        )
        assert failed_message is not None
        metadata = dict(failed_message.message_metadata)
        metadata.update(
            {
                "delivery_retry_count": 1,
                "delivery_rewrite_pending": False,
                "delivery_rewrite_outbox_id": "outbox-rewrite",
            }
        )
        failed_message.message_metadata = metadata
        await session.flush()
        assert await _handle_guest_delivery_failure(
            session,
            "wecom-msg-security",
            fail_type=13,
        ) is True
        jobs = list((await session.scalars(select(Job))).all())

        assert len(jobs) == 1
        assert jobs[0].job_type == "guest_delivery_rewrite"
        assert jobs[0].dedupe_key == "delivery-rewrite:1"
        assert jobs[0].payload == {"message_id": 1}
        assert blocked_content not in str(jobs[0].payload)

    await engine.dispose()


@pytest.mark.asyncio
async def test_delivery_rewrite_context_prefers_exact_source_guest() -> None:
    """即使失败回复后已有新问题，改写仍应读取原始关联问题。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        now = datetime(2026, 8, 21, tzinfo=UTC)
        original_guest = Message(
            conversation_id=conversation.id,
            external_message_id="guest-weather",
            origin=MessageOrigin.GUEST,
            message_type="text",
            content="明天天气",
            message_metadata={},
            sent_at=now,
        )
        failed_bot = Message(
            conversation_id=conversation.id,
            external_message_id="bot-weather",
            origin=MessageOrigin.BOT,
            message_type="text",
            content="武汉明天有阵雨。",
            message_metadata={"source_guest_message_id": "guest-weather"},
            sent_at=now,
        )
        newer_guest = Message(
            conversation_id=conversation.id,
            external_message_id="guest-newer",
            origin=MessageOrigin.GUEST,
            message_type="text",
            content="再问一下门票",
            message_metadata={},
            sent_at=now,
        )
        session.add_all([original_guest, failed_bot, newer_guest])
        await session.flush()
        await SQLAlchemyMessageRepository(session).mark_delivery_failed(
            "bot-weather",
            error_code="wecom_async_13",
        )

        context = await SQLAlchemyMessageRepository(
            session
        ).get_delivery_rewrite_context(failed_bot.id)

        assert context is not None
        assert context.source_guest.external_message_id == "guest-weather"
        assert context.source_guest.content == "明天天气"

    await engine.dispose()


@pytest.mark.asyncio
async def test_guest_outbox_carries_exact_source_message_id() -> None:
    """正常回复出站任务应携带原客人消息编号，供异步失败改写精确关联。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        outbox = TransactionalOutboxWeCom(
            session,
            source_message_id="guest-weather",
            source_guest_message_id="guest-weather",
            delivery_phase="final",
        )
        await outbox.send_text("wk-1", "wm-1", "武汉明天有阵雨。")
        job = await session.scalar(select(Job))

        assert job is not None
        assert job.payload["source_guest_message_id"] == "guest-weather"

    await engine.dispose()


@pytest.mark.asyncio
async def test_delivery_rewrite_service_commits_guard_before_real_outbox() -> None:
    """真实事务中应先提交单次调用标记，再登记一次可审计二次发送。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class RewriterStub:
        """返回事实等价但重新组织过的天气回复。"""

        async def rewrite(self, **kwargs) -> str:
            """验证仓储正文已传入并返回固定改写。"""
            assert kwargs["guest_question"] == "明天天气"
            return "8月22日武汉有阵雨，气温25～31℃。"

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        now = datetime(2026, 8, 21, tzinfo=UTC)
        guest = Message(
            conversation_id=conversation.id,
            external_message_id="guest-rewrite",
            origin=MessageOrigin.GUEST,
            message_type="text",
            content="明天天气",
            message_metadata={},
            sent_at=now,
        )
        failed_bot = Message(
            conversation_id=conversation.id,
            external_message_id="bot-rewrite",
            origin=MessageOrigin.BOT,
            message_type="text",
            content="武汉8月22日有阵雨，气温25～31℃。",
            message_metadata={"source_guest_message_id": "guest-rewrite"},
            sent_at=now,
        )
        session.add_all([guest, failed_bot])
        await session.flush()
        repository = SQLAlchemyMessageRepository(session)
        await repository.mark_delivery_failed(
            "bot-rewrite",
            error_code="wecom_async_13",
        )
        service = GuestDeliveryRewriteJobService(
            repository=repository,
            rewriter=RewriterStub(),
            outbox_factory=lambda message_id, guest_message_id: (
                TransactionalOutboxWeCom(
                    session,
                    source_message_id=f"delivery-rewrite:{message_id}",
                    source_guest_message_id=guest_message_id,
                    delivery_phase="guest",
                )
            ),
            before_model=session.commit,
            on_unavailable=lambda _message_id: session.commit(),
            agent_id=1000002,
            duty_employee_userids=["staff-1"],
        )

        await service.handle({"message_id": failed_bot.id})
        await session.commit()
        await session.refresh(failed_bot)
        jobs = list((await session.scalars(select(Job))).all())

        assert failed_bot.message_metadata["delivery_rewrite_started"] is True
        assert failed_bot.message_metadata["delivery_retry_count"] == 1
        assert len(jobs) == 1
        assert jobs[0].job_type == "wecom_send_text"
        assert jobs[0].payload["source_guest_message_id"] == "guest-rewrite"
        assert jobs[0].payload["retry_of_message_id"] == str(failed_bot.id)

    await engine.dispose()


@pytest.mark.asyncio
async def test_delivery_rewrite_context_supports_legacy_bot_without_source_link() -> None:
    """部署前旧回复缺少关联字段时，只回退到回复之前最近的客人文本。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        now = datetime(2026, 8, 21, tzinfo=UTC)
        guest = Message(
            conversation_id=conversation.id,
            external_message_id="legacy-guest",
            origin=MessageOrigin.GUEST,
            message_type="text",
            content="黄鹤楼门票多少钱",
            message_metadata={},
            sent_at=now,
        )
        failed_bot = Message(
            conversation_id=conversation.id,
            external_message_id="legacy-bot",
            origin=MessageOrigin.BOT,
            message_type="text",
            content="门票为70元。",
            message_metadata={},
            sent_at=now,
        )
        session.add_all([guest, failed_bot])
        await session.flush()
        await SQLAlchemyMessageRepository(session).mark_delivery_failed(
            "legacy-bot",
            error_code="wecom_async_13",
        )

        context = await SQLAlchemyMessageRepository(
            session
        ).get_delivery_rewrite_context(failed_bot.id)

        assert context is not None
        assert context.source_guest.content == "黄鹤楼门票多少钱"

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
async def test_terminal_delivery_compensation_clears_pending_and_notifies_once() -> None:
    """改写或二次发送终态失败应清 pending 并只登记一次员工通知。"""
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
            "bot-terminal-failure",
            "未成功送达的改写回复",
            metadata={
                "delivery_status": "failed",
                "delivery_retry_pending": True,
                "delivery_rewrite_pending": True,
            },
        )

        assert await _compensate_guest_delivery_failure(
            session,
            1,
            agent_id=1000002,
            employee_userids=["staff-1"],
        ) is True
        assert await _compensate_guest_delivery_failure(
            session,
            1,
            agent_id=1000002,
            employee_userids=["staff-1"],
        ) is False
        message = await session.get(Message, 1)
        jobs = list((await session.scalars(select(Job))).all())

        assert message is not None
        assert message.message_metadata["delivery_retry_pending"] is False
        assert message.message_metadata["delivery_rewrite_pending"] is False
        assert message.message_metadata["delivery_failure_notified"] is True
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


@pytest.mark.asyncio
async def test_real_repository_builds_one_contiguous_guest_batch_without_rewriting_rows() -> None:
    """真实消息仓储应按入库时间合并本轮片段，并保持每条原文独立保存。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    external_message_id="old",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="上一轮问题",
                    sent_at=now,
                    created_at=now,
                ),
                Message(
                    conversation_id=conversation.id,
                    external_message_id="msg-1",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="房间里的灯",
                    sent_at=now + timedelta(seconds=5),
                    created_at=now + timedelta(seconds=5),
                ),
                Message(
                    conversation_id=conversation.id,
                    external_message_id="msg-2",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="一直闪",
                    sent_at=now + timedelta(seconds=7),
                    created_at=now + timedelta(seconds=7),
                ),
                Message(
                    conversation_id=conversation.id,
                    external_message_id="msg-3",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="麻烦维修",
                    sent_at=now + timedelta(seconds=10),
                    created_at=now + timedelta(seconds=10),
                ),
            ]
        )
        await session.commit()
        messages = MessageService(SQLAlchemyMessageRepository(session))

        batch = await messages.build_guest_batch(conversation.id, "msg-3")
        stored_contents = list(
            await session.scalars(
                select(Message.content)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.id)
            )
        )

        assert batch.content == "房间里的灯\n一直闪\n麻烦维修"
        assert batch.message_count == 3
        assert stored_contents == ["上一轮问题", "房间里的灯", "一直闪", "麻烦维修"]

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin,message_type",
    [
        (MessageOrigin.GUEST, "image"),
        (MessageOrigin.SERVICER, "text"),
    ],
)
async def test_new_non_bot_activity_invalidates_older_debounce_boundary(
    origin: MessageOrigin,
    message_type: str,
) -> None:
    """真实仓储应把客人非文本和员工回复都视为更新会话活动。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    external_message_id="msg-1",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="请补矿泉水",
                    sent_at=now,
                ),
                Message(
                    conversation_id=conversation.id,
                    external_message_id="msg-2",
                    origin=origin,
                    message_type=message_type,
                    content="new activity",
                    sent_at=now + timedelta(seconds=1),
                ),
            ]
        )
        await session.commit()

        repository = SQLAlchemyMessageRepository(session)

        assert (
            await repository.has_newer_conversation_activity(
                conversation.id,
                "msg-1",
            )
            is True
        )

    await engine.dispose()
