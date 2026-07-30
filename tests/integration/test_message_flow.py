from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.domain.models import Base, Conversation, Customer, Message
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
