from dataclasses import dataclass

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.domain.models import Conversation, Message
from homestay_bot.services.message_service import IncomingMessage


@dataclass(frozen=True, slots=True)
class DeliveryRewriteContext:
    """保存一次安全改写所需的失败回复、原问题和会话。"""

    failed_bot: Message
    source_guest: Message
    conversation: Conversation


class SQLAlchemyConversationRepository:
    """使用 SQLAlchemy 创建、读取和更新唯一客服会话。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前数据库会话。"""
        self._session = session

    async def get_or_create(self, message: IncomingMessage) -> Conversation:
        """按客服账号和外部联系人查找会话，不存在时创建。"""
        statement = select(Conversation).where(
            Conversation.open_kfid == message.open_kfid,
            Conversation.external_userid == message.external_userid,
        )
        conversation = await self._session.scalar(statement)
        if conversation is not None:
            return conversation

        conversation = Conversation(
            open_kfid=message.open_kfid,
            external_userid=message.external_userid,
        )
        # begin_nested 会隐式刷新 pending 对象；先在捕获范围外暴露外层事务错误。
        await self._session.flush()
        try:
            async with self._session.begin_nested():
                self._session.add(conversation)
                await self._session.flush()
        except IntegrityError:
            # 并发补拉可能同时创建会话；保存点回滚后只重新读取竞争结果。
            conversation = await self._session.scalar(statement)
            if conversation is None:
                raise
        return conversation

    async def save(self, conversation: Conversation) -> None:
        """刷新会话模式、语言和接待员工等变更。"""
        self._session.add(conversation)
        await self._session.flush()

    async def lock_activity(self, conversation_id: int) -> None:
        """锁定会话活动行，串行化新入站与静默任务的检查和出站写入。"""
        await self._session.scalar(
            select(Conversation.id)
            .where(Conversation.id == conversation_id)
            .with_for_update()
        )


class SQLAlchemyMessageRepository:
    """使用 SQLAlchemy 持久化并查询已去重消息。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前数据库会话。"""
        self._session = session

    async def exists(self, external_message_id: str) -> bool:
        """按企业微信消息编号判断是否已经处理。"""
        statement = select(Message.id).where(
            Message.external_message_id == external_message_id
        )
        return await self._session.scalar(statement) is not None

    async def add(self, message: Message) -> bool:
        """保存消息并刷新主键；唯一键竞争返回 False 且不污染外层事务。"""
        # 只允许保存点内部的消息唯一键错误按重复处理，不能吞掉外层约束错误。
        await self._session.flush()
        try:
            async with self._session.begin_nested():
                self._session.add(message)
                await self._session.flush()
        except IntegrityError:
            # 只有外部消息编号已存在才属于幂等重复；外键、非空等错误必须上抛。
            if await self.exists(message.external_message_id):
                return False
            raise
        return True

    async def list_recent(
        self,
        conversation_id: int,
        limit: int,
        through_external_message_id: str | None = None,
    ) -> list[Message]:
        """按系统实际处理顺序读取最近消息，避免外部时区扰乱上下文。"""
        conditions = [
            Message.conversation_id == conversation_id,
            Message.message_type == "text",
        ]
        if through_external_message_id is not None:
            boundary = select(Message.id).where(
                Message.external_message_id == through_external_message_id
            ).scalar_subquery()
            conditions.append(Message.id <= boundary)
        statement = (
            select(Message)
            .where(*conditions)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        recent = list((await self._session.scalars(statement)).all())
        recent.reverse()
        return recent

    async def has_newer_guest_message(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源消息之后是否已保存更新的客人文本。"""
        boundary = select(Message.id).where(
            Message.external_message_id == external_message_id
        ).scalar_subquery()
        statement = select(
            exists().where(
                Message.conversation_id == conversation_id,
                Message.id > boundary,
                Message.origin == MessageOrigin.GUEST,
                Message.message_type == "text",
            )
        )
        return bool(await self._session.scalar(statement))

    async def has_newer_conversation_activity(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源边界后是否出现任意客人或员工活动。"""
        boundary = select(Message.id).where(
            Message.external_message_id == external_message_id
        ).scalar_subquery()
        statement = select(
            exists().where(
                Message.conversation_id == conversation_id,
                Message.id > boundary,
                Message.origin != MessageOrigin.BOT,
            )
        )
        return bool(await self._session.scalar(statement))

    async def replace_external_message_id(
        self, temporary_id: str, external_message_id: str
    ) -> None:
        """发送成功后用企业微信真实 msgid 替换 outbox 临时编号。"""
        await self._session.execute(
            update(Message)
            .where(Message.external_message_id == temporary_id)
            .values(external_message_id=external_message_id)
        )
        await self._session.flush()

    async def mark_delivery_failed(
        self,
        external_message_id: str,
        *,
        error_code: str,
    ) -> Message | None:
        """记录企业微信异步投递失败，供重试编排和上下文过滤使用。"""
        message = await self._session.scalar(
            select(Message).where(Message.external_message_id == external_message_id)
        )
        if message is None or message.origin is not MessageOrigin.BOT:
            return message
        metadata = dict(message.message_metadata or {})
        try:
            retry_count = int(metadata.get("delivery_retry_count", 0))
        except (TypeError, ValueError):
            retry_count = 0
        metadata.update(
            {
                "delivery_status": "failed",
                "delivery_error_code": error_code[:64],
                "delivery_retry_count": max(retry_count, 0),
            }
        )
        message.message_metadata = metadata
        await self._session.flush()
        return message

    async def get_delivery_rewrite_context(
        self,
        failed_bot_id: int,
    ) -> DeliveryRewriteContext | None:
        """读取首次安全拦截回复及其精确关联的客人问题。"""
        failed_bot = await self._session.get(Message, failed_bot_id)
        if (
            failed_bot is None
            or failed_bot.origin is not MessageOrigin.BOT
            or not failed_bot.content
        ):
            return None
        metadata = dict(failed_bot.message_metadata or {})
        if (
            metadata.get("delivery_status") != "failed"
            or metadata.get("delivery_error_code") != "wecom_async_13"
        ):
            return None
        conversation = await self._session.get(
            Conversation,
            failed_bot.conversation_id,
        )
        if conversation is None:
            return None

        source_guest: Message | None = None
        source_external_id = str(metadata.get("source_guest_message_id", "")).strip()
        if source_external_id:
            source_guest = await self._session.scalar(
                select(Message).where(
                    Message.conversation_id == failed_bot.conversation_id,
                    Message.external_message_id == source_external_id,
                    Message.origin == MessageOrigin.GUEST,
                    Message.message_type == "text",
                    Message.content.is_not(None),
                )
            )
        if source_guest is None:
            # 兼容部署前没有精确关联字段的旧消息，只回退到失败回复之前的最近问题。
            source_guest = await self._session.scalar(
                select(Message)
                .where(
                    Message.conversation_id == failed_bot.conversation_id,
                    Message.id < failed_bot.id,
                    Message.origin == MessageOrigin.GUEST,
                    Message.message_type == "text",
                    Message.content.is_not(None),
                )
                .order_by(Message.id.desc())
                .limit(1)
            )
        if source_guest is None or not source_guest.content:
            return None
        return DeliveryRewriteContext(
            failed_bot=failed_bot,
            source_guest=source_guest,
            conversation=conversation,
        )

    async def save_delivery_rewrite_metadata(
        self,
        message: Message,
        metadata: dict[str, object],
    ) -> None:
        """保存原失败回复的改写及二次投递审计字段。"""
        # JSON 字段在模型调用前已经提交；每次赋新对象才能触发 SQLAlchemy 脏检查。
        message.message_metadata = dict(metadata)
        await self._session.flush()
