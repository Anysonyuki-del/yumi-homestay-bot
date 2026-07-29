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

        assert first is True
        assert second is False

    await engine.dispose()
