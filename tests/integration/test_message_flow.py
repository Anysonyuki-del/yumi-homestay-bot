from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.domain.models import Base
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
