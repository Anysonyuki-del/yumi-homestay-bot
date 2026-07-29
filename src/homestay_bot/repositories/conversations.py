from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def save(self, conversation: Conversation) -> None:
        """刷新会话模式、语言和接待员工等变更。"""
        self._session.add(conversation)
        await self._session.flush()


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

    async def add(self, message: Message) -> None:
        """保存消息并刷新主键，提交由调用方负责。"""
        self._session.add(message)
        await self._session.flush()

    async def list_recent(
        self, conversation_id: int, limit: int
    ) -> list[Message]:
        """按系统实际处理顺序读取最近消息，避免外部时区扰乱上下文。"""
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        recent = list((await self._session.scalars(statement)).all())
        recent.reverse()
        return recent

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
