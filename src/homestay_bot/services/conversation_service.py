import logging
import re
from datetime import datetime
from typing import Protocol

from pydantic import ValidationError

from homestay_bot.domain.enums import ConversationMode, Language, MessageOrigin
from homestay_bot.domain.models import BookingApproval, Conversation
from homestay_bot.domain.schemas import BookingRequest
from homestay_bot.integrations.deepseek_client import (
    AssistantDecision,
    AssistantUnavailableError,
)
from homestay_bot.integrations.tourism import TourismSearchError
from homestay_bot.services.emergency_service import (
    EmergencyClassification,
    EmergencyService,
)
from homestay_bot.services.message_service import IncomingMessage

_MAX_ASSISTANT_REPLY_CHARACTERS = 1500
logger = logging.getLogger(__name__)


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

    async def build_context(
        self, conversation_id: int, limit: int = 20
    ) -> list[dict[str, str]]:
        """返回有限的客人与机器人历史上下文。"""


class GuestAssistantPort(Protocol):
    """定义会话层调用客服模型的最小接口。"""

    async def respond(
        self,
        *,
        guest_identifier: str,
        language: Language,
        messages: list[dict[str, str]],
    ) -> AssistantDecision:
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


class PendingApprovalPort(Protocol):
    """定义客人确认资料后创建待审批单的唯一入口。"""

    async def create_pending(
        self, conversation_id: int, request: BookingRequest
    ) -> BookingApproval:
        """只创建待审批单，不执行百居易写入。"""


class FrequentFaqPort(Protocol):
    """定义会话层登记高频 FAQ 候选的最小接口。"""

    async def track(
        self,
        *,
        source_message_id: str,
        question: str,
        occurred_at: datetime,
        decision: AssistantDecision,
    ) -> None:
        """在客人回复后记录一次可能的知识候选。"""


class ConversationService:
    """按来源、会话状态和风险规则编排机器人与人工处理。"""

    _handoff_pattern = re.compile(
        r"人工客服|转人工|找人工|工作人员接待|"
        r"human agent|live agent|staff member",
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
        approvals: PendingApprovalPort | None = None,
        approval_base_url: str = "",
        frequent_faq: FrequentFaqPort | None = None,
    ) -> None:
        """注入仓储、AI、安全分类器和企业微信发送端口。"""
        self._conversations = conversations
        self._messages = messages
        self._assistant = assistant
        self._emergency = emergency_service
        self._wecom = wecom
        self._agent_id = agent_id
        self._duty_employee_userids = duty_employee_userids
        self._approvals = approvals
        self._approval_base_url = approval_base_url.rstrip("/")
        self._frequent_faq = frequent_faq

    async def handle_message(self, message: IncomingMessage) -> None:
        """处理单条已去重消息，确保人工回复不会形成机器人回环。"""
        conversation = await self._conversations.get_or_create(message)
        if not await self._messages.record_incoming(conversation.id, message):
            return
        if message.origin is MessageOrigin.SERVICER:
            # 一旦人工客服发言，立即锁定人工模式，防止客人下一条消息又触发机器人。
            if conversation.mode is not ConversationMode.HUMAN_ACTIVE:
                conversation.mode = ConversationMode.HUMAN_ACTIVE
                await self._conversations.save(conversation)
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

        try:
            decision = await self._assistant.respond(
                guest_identifier=message.external_userid,
                language=conversation.language,
                messages=await self._messages.build_context(conversation.id),
            )
        except TourismSearchError as error:
            await self._escalate_tourism_failure(conversation, message, error)
            return
        except AssistantUnavailableError:
            await self._escalate_assistant_failure(conversation, message)
            return
        reply_text = self._limit_assistant_reply(decision.reply_text)
        await self._send_guest_reply(conversation, reply_text)
        await self._track_frequent_faq(message, decision)
        if decision.intent == "booking_confirmed":
            await self._create_pending_approval(conversation, message, decision)
            return
        # 未确认的交易事实只提醒员工核实，机器人继续承接后续对话。
        if decision.staff_confirmation_required:
            await self._notify_employee(
                conversation,
                message,
                (
                    "业务待确认"
                    "\n原因："
                    f"{decision.staff_confirmation_reason or 'transaction_unconfirmed'}"
                ),
            )
            return

    async def _track_frequent_faq(
        self,
        message: IncomingMessage,
        decision: AssistantDecision,
    ) -> None:
        """隔离候选统计异常，确保客人回复和会话状态不受影响。"""
        if self._frequent_faq is None:
            return
        try:
            await self._frequent_faq.track(
                source_message_id=message.msgid,
                question=message.content,
                occurred_at=message.sent_at,
                decision=decision,
            )
        except Exception as error:
            # 不记录问题正文和外部联系人，只保留异常类型用于诊断。
            logger.warning(
                "高频 FAQ 统计失败，已保留客人回复：error_type=%s",
                type(error).__name__,
            )

    async def _create_pending_approval(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        decision: AssistantDecision,
    ) -> None:
        """把模型提取的完整资料转换为待审批单，缺项时强制转人工。"""
        fields = decision.booking_fields
        if self._approvals is None or fields is None:
            await self._activate_human(
                conversation, message, "预订资料无法生成待审批单"
            )
            return
        try:
            request = BookingRequest.model_validate(fields.model_dump())
        except ValidationError:
            await self._activate_human(
                conversation, message, "预订资料不完整或格式无效"
            )
            return

        approval = await self._approvals.create_pending(conversation.id, request)
        conversation.mode = ConversationMode.HUMAN_ACTIVE
        await self._conversations.save(conversation)
        await self._notify_employee(
            conversation,
            message,
            (
                f"新待审批单：{approval.approval_code}（ID {approval.id}）\n"
                f"{self._approval_base_url}/employee/approvals/{approval.id}"
            ),
        )

    async def _activate_human(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        reason: str,
    ) -> None:
        """切换人工模式并发送内部原因摘要。"""
        conversation.mode = ConversationMode.HUMAN_ACTIVE
        await self._conversations.save(conversation)
        await self._notify_employee(conversation, message, reason)

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

    @staticmethod
    def _limit_assistant_reply(content: str) -> str:
        """把精简后的客人可见回复限制为最多一千五百个字符。"""
        if len(content) <= _MAX_ASSISTANT_REPLY_CHARACTERS:
            return content
        return content[: _MAX_ASSISTANT_REPLY_CHARACTERS - 1] + "…"

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

    async def _escalate_tourism_failure(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        error: TourismSearchError,
    ) -> None:
        """明确告知联网失败，再切人工并通知值班员工。"""
        reply = (
            "I’m unable to check live travel information right now. "
            "A staff member has been notified to help you."
            if conversation.language is Language.EN
            else "暂时无法查询实时旅游信息，已为您通知工作人员协助，请稍候。"
        )
        await self._send_guest_reply(conversation, reply)
        conversation.mode = ConversationMode.HUMAN_ACTIVE
        await self._conversations.save(conversation)
        await self._notify_employee(
            conversation,
            message,
            f"旅游联网失败：{error.status}",
        )

    async def _escalate_assistant_failure(
        self,
        conversation: Conversation,
        message: IncomingMessage,
    ) -> None:
        """告知普通模型暂不可用，再切人工并通知值班员工。"""
        reply = (
            "I’m temporarily unable to process this request. "
            "A staff member has been notified to help you."
            if conversation.language is Language.EN
            else "暂时无法处理这个问题，已为您通知工作人员协助，请稍候。"
        )
        await self._send_guest_reply(conversation, reply)
        conversation.mode = ConversationMode.HUMAN_ACTIVE
        await self._conversations.save(conversation)
        await self._notify_employee(
            conversation,
            message,
            "模型服务暂时不可用",
        )

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
