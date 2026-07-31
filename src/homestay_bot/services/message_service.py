from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.domain.models import Message


@dataclass(frozen=True)
class IncomingMessage:
    """表示完成企业微信字段转换后的统一入站消息。"""

    msgid: str
    open_kfid: str
    external_userid: str
    origin: MessageOrigin
    msgtype: str
    content: str
    sent_at: datetime


class MessageRepository(Protocol):
    """定义消息去重与持久化所需仓储接口。"""

    async def exists(self, external_message_id: str) -> bool:
        """判断外部消息编号是否已经保存。"""

    async def add(self, message: Message) -> None:
        """保存一条入站或机器人消息。"""

    async def list_recent(
        self, conversation_id: int, limit: int
    ) -> list[Message]:
        """按时间正序返回最近消息。"""


class MessageService:
    """统一保存企业微信入站消息和机器人出站消息。"""

    def __init__(self, repository: MessageRepository) -> None:
        """注入消息仓储。"""
        self._repository = repository

    async def record_incoming(
        self, conversation_id: int, incoming: IncomingMessage
    ) -> bool:
        """只保存首次出现的消息编号，并向编排层返回去重结果。"""
        if await self._repository.exists(incoming.msgid):
            return False
        await self._repository.add(
            Message(
                conversation_id=conversation_id,
                external_message_id=incoming.msgid,
                origin=incoming.origin,
                message_type=incoming.msgtype,
                content=incoming.content,
                sent_at=incoming.sent_at,
            )
        )
        return True

    async def record_bot(
        self,
        conversation_id: int,
        message_id: str,
        content: str,
        sent_at: datetime | None = None,
        message_type: str = "text",
    ) -> None:
        """保存机器人已发送文本，便于审计和恢复对话上下文。"""
        await self._repository.add(
            Message(
                conversation_id=conversation_id,
                external_message_id=message_id,
                origin=MessageOrigin.BOT,
                message_type=message_type,
                content=content,
                # 企业微信入站时间统一为 UTC，机器人消息也必须使用同一时间基准。
                sent_at=sent_at or datetime.now(UTC),
            )
        )

    async def build_context(
        self, conversation_id: int, limit: int = 20
    ) -> list[dict[str, str]]:
        """返回最近客人与机器人文本，人工消息绝不重新交给模型。"""
        messages = await self._repository.list_recent(conversation_id, limit)
        context: list[dict[str, str]] = []
        for message in messages:
            if message.message_type != "text" or not message.content:
                continue
            if message.origin is MessageOrigin.GUEST:
                context.append({"role": "user", "content": message.content})
            elif message.origin is MessageOrigin.BOT:
                context.append({"role": "assistant", "content": message.content})
        return context
