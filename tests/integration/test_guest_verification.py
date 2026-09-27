from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import Language, MessageOrigin
from homestay_bot.domain.models import Base, BusinessTask, Conversation, Customer, Message
from homestay_bot.repositories.conversations import (
    SQLAlchemyConversationRepository,
    SQLAlchemyMessageRepository,
)
from homestay_bot.services.context_retention import ContextRetentionService
from homestay_bot.services.guest_verification import GuestVerificationService
from homestay_bot.services.message_service import (
    IncomingMessage,
    MessageService,
    model_message_content,
)


@pytest.mark.parametrize("details", ["1234 合成客人", "预订人合成客人，手机号后四位1234"])
@pytest.mark.asyncio
async def test_verification_is_retained_but_isolated_from_model_and_next_batch(details):
    """资料仅在核对任务明文显示；直接上下文、摘要和后续合并均不能泄漏。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(Customer(id=1, display_name="合成客人"))
            await session.flush()
            conversation = Conversation(
                id=1, customer_id=1, open_kfid="test", external_userid="test"
            )
            session.add(conversation)
            await session.flush()
            messages = MessageService(SQLAlchemyMessageRepository(session))
            verification = GuestVerificationService(session)
            now = datetime.now(UTC)

            def incoming(key, text):
                """构造隔离客人的合成输入。"""
                return IncomingMessage(key, "test", "test", MessageOrigin.GUEST, "text", text, now)

            for key, text in [("request", "请帮我改期"), ("details", details)]:
                event = incoming(key, text)
                await messages.record_incoming(1, event)
                result = await verification.handle(conversation, event)
                assert result
            task = await session.get(BusinessTask, result[0])
            assert task.verification_data["value"] == details
            rows = await SQLAlchemyMessageRepository(session).list_recent(1, 10)
            detail = next(row for row in rows if row.external_message_id == "details")
            assert detail.content == details
            assert model_message_content(detail) == "[核对信息已转交管家]"
            context = await messages.build_context(1, limit=10)
            assert "1234" not in str(context)
            sources = ContextRetentionService._sources([detail])
            assert "1234" not in str(sources)
            await messages.record_incoming(1, incoming("next", "早餐有什么规定"))
            batch = await messages.build_guest_batch(1, "next")
            assert batch.content == "早餐有什么规定"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_language_uses_two_substantive_messages_and_ignores_short_replies():
    """首个有效消息定语言，短回复不变，连续两条不同语言才切换。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            conversation = Conversation(id=1, open_kfid="test", external_userid="test")
            session.add(conversation)
            await session.flush()
            repository = SQLAlchemyConversationRepository(session)
            expected = [Language.ZH, Language.ZH, Language.ZH, Language.ZH, Language.EN]
            language = Language.EN
            for index, text in enumerate(
                ["你好我要入住", "ok", "123", "Where is breakfast", "What time is breakfast"]
            ):
                session.add(
                    Message(
                        conversation_id=1,
                        external_message_id=str(index),
                        origin=MessageOrigin.GUEST,
                        message_type="text",
                        content=text,
                        sent_at=datetime.now(UTC),
                    )
                )
                await session.flush()
                language = await repository.detect_language(1, language)
                assert language is expected[index]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_refund_verification_never_downgrades_handoff_and_masks_inline_details():
    """客诉核对资料不把接管降级，首次申请自带订单号也不泄漏到后续模型。"""
    from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
    from homestay_bot.services.complaint_service import ComplaintService
    from homestay_bot.services.conversation_service import ConversationService
    from homestay_bot.services.emergency_service import EmergencyService
    from tests.unit.test_conversation_service import AssistantStub, WeComStub

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(Customer(id=1, display_name="合成客人"))
            await session.flush()
            conversation = Conversation(
                id=1,
                customer_id=1,
                open_kfid="test",
                external_userid="test",
            )
            session.add(conversation)
            await session.flush()
            messages = MessageService(SQLAlchemyMessageRepository(session))
            audits = SQLAlchemyOperationsRepository(session)
            sender = WeComStub()
            service = ConversationService(
                conversations=SQLAlchemyConversationRepository(session),
                messages=messages,
                assistant=AssistantStub(),
                emergency_service=EmergencyService(),
                wecom=sender,
                agent_id=1,
                duty_employee_userids=["synthetic"],
                verification=GuestVerificationService(session),
                audit_events=audits,
                complaint_service=ComplaintService(),
            )
            for key, text in [
                ("refund", "我要退款"),
                ("details", "预订人合成客人，手机号后四位1234"),
            ]:
                await service.handle_message(
                    IncomingMessage(
                        key,
                        "test",
                        "test",
                        MessageOrigin.GUEST,
                        "text",
                        text,
                        datetime.now(UTC),
                    )
                )
                await session.commit()
            assert await audits.latest_handoff_reason(1) == "complaint:refund"
            assert "1234" not in str(sender.internal_messages)
            assert "1234" not in str(await messages.build_context(1, limit=10))
            # 另一次订单核对可在首条申请中携带资料。
            event = IncomingMessage(
                "inline",
                "test",
                "test",
                MessageOrigin.GUEST,
                "text",
                "我要改期，订单号AB1234",
                datetime.now(UTC),
            )
            await service.handle_message(event)
            assert "AB1234" not in str(await messages.build_context(1, limit=10))
    finally:
        await engine.dispose()
