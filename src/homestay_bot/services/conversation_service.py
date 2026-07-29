import re
from typing import Any, Protocol

from homestay_bot.domain.enums import ConversationMode, Language, MessageOrigin
from homestay_bot.domain.models import Conversation
from homestay_bot.integrations.openai_client import AssistantDecision
from homestay_bot.services.emergency_service import (
    EmergencyClassification,
    EmergencyService,
)
from homestay_bot.services.message_service import IncomingMessage


class ConversationRepository(Protocol):
    """定义会话编排所需的持久化接口。"""

    async def get_or_create(self, message: IncomingMessage) -> Conversation:
        """查找或创建客人会话。"""

    async def save(self, conversation: Conversation) -> None:
        """保存会话状态。"""


class ConversationMessageService(Protocol):
    """定义编排层所需的消息记录接口。"""

    async def record_incoming(
        self, conversation_id: int, message: IncomingMessage
    ) -> bool:
        """保存入站消息并返回是否为新消息。"""

    async def record_bot(
        self, conversation_id: int, message_id: str, content: str
    ) -> None:
        """保存机器人出站消息。"""


class GuestAssistantPort(Protocol):
    """定义会话层调用客服模型的最小接口。"""

    async def respond(self, **kwargs: Any) -> AssistantDecision:
        """返回经过结构校验的客服决定。"""


class WeComMessagingPort(Protocol):
    """定义会话层发送客人消息和员工通知的企业微信接口。"""

    async def send_text(
        self, open_kfid: str, external_userid: str, content: str
    ) -> str:
        """发送客人文本并返回消息编号。"""

    async def send_internal_text(
        self,
        *,
        agent_id: int,
        employee_userids: list[str],
        content: str,
    ) -> None:
        """通知值班员工处理人工会话。"""


class ConversationService:
    """按来源、会话状态和风险规则编排机器人与人工处理。"""

    _handoff_pattern = re.compile(
        r"人工客服|转人工|投诉|差评|退款|取消|改期|付款争议|价格争议|"
        r"human agent|live agent|complaint|refund|cancel|reschedule|payment dispute",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        messages: ConversationMessageService,
        assistant: GuestAssistantPort,
        emergency_service: EmergencyService,
        wecom: WeComMessagingPort,
        agent_id: int,
        duty_employee_userids: list[str],
    ) -> None:
        """注入仓储、AI、安全分类器和企业微信发送端口。"""
        self._conversations = conversations
        self._messages = messages
        self._assistant = assistant
        self._emergency = emergency_service
        self._wecom = wecom
        self._agent_id = agent_id
        self._duty_employee_userids = duty_employee_userids

    async def handle_message(self, message: IncomingMessage) -> None:
        """处理单条已去重消息，确保人工回复不会形成机器人回环。"""
        conversation = await self._conversations.get_or_create(message)
        if not await self._messages.record_incoming(conversation.id, message):
            return
        if message.origin is MessageOrigin.SERVICER:
            return
        if message.origin is not MessageOrigin.GUEST:
            return

        language = self._detect_language(message.content, conversation.language)
        if language is not conversation.language:
            conversation.language = language
            await self._conversations.save(conversation)

        emergency = self._emergency.classify(message.content)
        if emergency.is_emergency:
            await self._escalate_emergency(conversation, message, emergency)
            return

        if conversation.mode is ConversationMode.HUMAN_ACTIVE:
            await self._notify_employee(conversation, message, "人工接待会话收到新消息")
            return

        if message.msgtype != "text" or self._handoff_pattern.search(message.content):
            await self._escalate_regular(conversation, message)
            return

        decision = await self._assistant.respond(
            guest_identifier=message.external_userid,
            language=conversation.language,
            messages=[{"role": "user", "content": message.content}],
        )
        await self._send_guest_reply(conversation, decision.reply_text)
        if decision.handoff_reason is not None:
            conversation.mode = ConversationMode.HUMAN_ACTIVE
            await self._conversations.save(conversation)
            await self._notify_employee(
                conversation,
                message,
                f"机器人请求人工接管：{decision.handoff_reason}",
            )

    @staticmethod
    def _detect_language(text: str, fallback: Language) -> Language:
        """包含中文时使用中文，否则英文字符占主导时使用英文。"""
        if re.search(r"[\u4e00-\u9fff]", text):
            return Language.ZH
        if re.search(r"[A-Za-z]", text):
            return Language.EN
        return fallback

    async def _send_guest_reply(
        self, conversation: Conversation, content: str
    ) -> None:
        """发送并持久化机器人文本。"""
        message_id = await self._wecom.send_text(
            conversation.open_kfid,
            conversation.external_userid,
            content,
        )
        await self._messages.record_bot(conversation.id, message_id, content)

    async def _escalate_emergency(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        emergency: EmergencyClassification,
    ) -> None:
        """发送固定安全提示、切人工并通知值班员工。"""
        reply = self._emergency.safety_reply(emergency, conversation.language)
        await self._send_guest_reply(conversation, reply)
        conversation.mode = ConversationMode.HUMAN_ACTIVE
        await self._conversations.save(conversation)
        await self._notify_employee(
            conversation,
            message,
            f"紧急事件：{emergency.category}",
        )

    async def _escalate_regular(
        self, conversation: Conversation, message: IncomingMessage
    ) -> None:
        """对媒体、投诉和客人主动要求人工等情况执行普通接管。"""
        reply = (
            "A staff member has been notified and will assist you shortly."
            if conversation.language is Language.EN
            else "已为您通知工作人员，请稍候，我们会尽快人工处理。"
        )
        await self._send_guest_reply(conversation, reply)
        conversation.mode = ConversationMode.HUMAN_ACTIVE
        await self._conversations.save(conversation)
        await self._notify_employee(conversation, message, "普通人工接管")

    async def _notify_employee(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        reason: str,
    ) -> None:
        """向值班员工发送不包含接口密钥的会话摘要。"""
        await self._wecom.send_internal_text(
            agent_id=self._agent_id,
            employee_userids=self._duty_employee_userids,
            content=(
                f"{reason}\n客服账号：{conversation.open_kfid}\n"
                f"客人：{conversation.external_userid}\n消息：{message.content[:500]}"
            ),
        )
