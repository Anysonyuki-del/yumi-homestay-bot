from sqlalchemy import select
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
