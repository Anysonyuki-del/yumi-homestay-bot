from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.domain.models import Conversation, Message
from homestay_bot.services.message_service import IncomingMessage


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
