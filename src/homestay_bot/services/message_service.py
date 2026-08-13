from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

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
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class GuestMessageBatch:
    """表示静默窗口内按入库顺序合并的一组客人文本。"""

    content: str
    message_count: int


class MessageRepository(Protocol):
    """定义消息去重与持久化所需仓储接口。"""

    async def exists(self, external_message_id: str) -> bool:
        """判断外部消息编号是否已经保存。"""

    async def add(self, message: Message) -> bool:
        """保存一条入站或机器人消息；唯一键竞争返回 False。"""

    async def list_recent(
        self,
        conversation_id: int,
        limit: int,
        through_external_message_id: str | None = None,
    ) -> list[Message]:
        """按时间正序返回指定来源消息之前的最近消息。"""

    async def has_newer_guest_message(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源消息之后是否已经出现更新的客人问题。"""

    async def has_newer_conversation_activity(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源消息之后是否出现客人或员工的新活动。"""


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
        return await self._repository.add(
            Message(
                conversation_id=conversation_id,
                external_message_id=incoming.msgid,
                origin=incoming.origin,
                message_type=incoming.msgtype,
                content=incoming.content,
                message_metadata=incoming.metadata or {},
                sent_at=incoming.sent_at,
            )
        )

    async def record_bot(
        self,
        conversation_id: int,
        message_id: str,
        content: str,
        sent_at: datetime | None = None,
        message_type: str = "text",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """保存机器人已发送文本，便于审计和恢复对话上下文。"""
        if await self._repository.exists(message_id):
            return
        await self._repository.add(
            Message(
                conversation_id=conversation_id,
                external_message_id=message_id,
                origin=MessageOrigin.BOT,
                message_type=message_type,
                content=content,
                message_metadata=metadata or {},
                # 企业微信入站时间统一为 UTC，机器人消息也必须使用同一时间基准。
                sent_at=sent_at or datetime.now(UTC),
            )
        )

    async def build_context(
        self,
        conversation_id: int,
        limit: int = 20,
        through_external_message_id: str | None = None,
        *,
        merged_guest_content: str | None = None,
        merged_guest_count: int = 1,
    ) -> list[dict[str, str]]:
        """返回最近客人与机器人文本，人工消息绝不重新交给模型。"""
        query_limit = limit + max(0, merged_guest_count - 1)
        messages = await self._repository.list_recent(
            conversation_id,
            query_limit,
            through_external_message_id,
        )
        context: list[dict[str, str]] = []
        for message in messages:
            if message.message_type != "text" or not message.content:
                continue
            if message.origin is MessageOrigin.GUEST:
                context.append({"role": "user", "content": message.content})
            elif message.origin is MessageOrigin.BOT:
                # 企业微信异步失败回执确认未送达，不能让模型误以为客人已看到。
                if message.message_metadata.get("delivery_status") == "failed":
                    continue
                context.append({"role": "assistant", "content": message.content})
        if (
            merged_guest_content is not None
            and merged_guest_count > 1
            and len(context) >= merged_guest_count
            and all(
                item["role"] == "user" for item in context[-merged_guest_count:]
            )
        ):
            # 只折叠当前尾部连续客人片段，不能越过上一轮机器人回复。
            context = context[:-merged_guest_count]
            context.append({"role": "user", "content": merged_guest_content})
        return context[-limit:]

    async def build_guest_batch(
        self,
        conversation_id: int,
        through_external_message_id: str,
        *,
        quiet_window_seconds: int = 3,
        max_messages: int = 10,
        max_characters: int = 2000,
    ) -> GuestMessageBatch:
        """合并来源边界前的连续客人文本，同时限制消息数和总字符数。"""
        recent = await self._repository.list_recent(
            conversation_id,
            max_messages,
            through_external_message_id,
        )
        selected: list[Message] = []
        newer_received_at: datetime | None = None
        quiet_window = timedelta(seconds=quiet_window_seconds)
        for message in reversed(recent):
            if (
                message.origin is not MessageOrigin.GUEST
                or message.message_type != "text"
                or not message.content
            ):
                break
            received_at = self._received_at(message)
            if (
                newer_received_at is not None
                and newer_received_at - received_at > quiet_window
            ):
                break
            selected.append(message)
            newer_received_at = received_at
        selected.reverse()
        merged = "\n".join(str(message.content) for message in selected)
        # 超长批次优先保留最新文本，避免最后的完整问题被较早片段挤掉。
        bounded = merged[-max_characters:]
        return GuestMessageBatch(
            content=bounded,
            message_count=len(selected),
        )

    @staticmethod
    def _received_at(message: Message) -> datetime:
        """优先使用系统入库时间，并统一为可安全相减的 UTC 时间。"""
        received_at = getattr(message, "created_at", None) or message.sent_at
        if received_at.tzinfo is None:
            return received_at.replace(tzinfo=UTC)
        return received_at.astimezone(UTC)

    async def has_newer_guest_message(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源消息之后是否已经保存新的客人文本。"""
        return await self._repository.has_newer_guest_message(
            conversation_id,
            external_message_id,
        )

    async def has_newer_conversation_activity(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源消息之后是否出现任意非机器人会话活动。"""
        return await self._repository.has_newer_conversation_activity(
            conversation_id,
            external_message_id,
        )
