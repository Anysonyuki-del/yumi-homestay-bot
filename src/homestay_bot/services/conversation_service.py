import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Protocol

from pydantic import ValidationError

from homestay_bot.domain.enums import (
    BusinessTaskType,
    ConversationMode,
    Language,
    MessageOrigin,
)
from homestay_bot.domain.models import (
    BookingApproval,
    BusinessTask,
    Conversation,
    Customer,
)
from homestay_bot.domain.schemas import BookingRequest
from homestay_bot.integrations.deepseek_client import (
    AssistantDecision,
    AssistantUnavailableError,
    TaskSuggestion,
)
from homestay_bot.integrations.tourism import TourismSearchError
from homestay_bot.services.answer_policy import (
    handoff_reason as determine_handoff_reason,
)
from homestay_bot.services.answer_policy import (
    is_homestay_related,
)
from homestay_bot.services.context_retention import CustomerModelContext
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
        self,
        conversation_id: int,
        message_id: str,
        content: str,
        sent_at: datetime | None = None,
        message_type: str = "text",
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
        customer_context: CustomerModelContext | None = None,
    ) -> AssistantDecision:
        """返回经过结构校验的客服决定。"""

    async def respond_ack(
        self,
        *,
        guest_identifier: str,
        language: Language,
        question: str,
    ) -> str:
        """返回无工具的快速温暖安抚。"""


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


class CustomerProfilePort(Protocol):
    """定义会话进入消息上下文前建立正式客户的接口。"""

    async def ensure_for_message(self, message: IncomingMessage) -> Customer:
        """幂等建立客户并返回正式主档。"""


class CustomerContextPort(Protocol):
    """定义按正式客户读取脱敏摘要的接口。"""

    async def load_model_context(self, customer_id: int) -> CustomerModelContext:
        """返回不含原文和敏感字段的客户摘要。"""


class BusinessTaskPort(Protocol):
    """定义会话层保存 AI 待确认任务的最小入口。"""

    async def record_ai_suggestion(
        self,
        *,
        customer_id: int,
        source_message_id: str,
        task_type: BusinessTaskType,
        description: str,
        property_id: int | None = None,
        service_date: date | None = None,
    ) -> BusinessTask:
        """幂等保存一条结构化任务建议。"""


class ConversationAuditPort(Protocol):
    """定义人工接管动作的安全审计入口。"""

    async def record_handoff(
        self,
        *,
        conversation_id: int,
        customer_id: int | None,
        reason: str,
    ) -> None:
        """只记录内部主键和原因代码。"""


class ConversationJobPort(Protocol):
    """定义会话阶段任务的持久化入口。"""

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, object],
        *,
        dedupe_key: str | None = None,
    ) -> object:
        """登记可恢复的后台任务。"""


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
        customer_profiles: CustomerProfilePort | None = None,
        customer_context: CustomerContextPort | None = None,
        business_tasks: BusinessTaskPort | None = None,
        audit_events: ConversationAuditPort | None = None,
        jobs: ConversationJobPort | None = None,
        defer_model: bool = False,
        commit_boundary: Callable[[], Awaitable[None]] | None = None,
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
        self._customer_profiles = customer_profiles
        self._customer_context = customer_context
        self._business_tasks = business_tasks
        self._audit_events = audit_events
        self._jobs = jobs
        self._defer_model = defer_model
        self._commit_boundary = commit_boundary

    async def handle_message(self, message: IncomingMessage) -> None:
        """处理单条已去重消息，确保人工回复不会形成机器人回环。"""
        conversation = await self._conversations.get_or_create(message)
        if self._customer_profiles is not None:
            customer = await self._customer_profiles.ensure_for_message(message)
            if conversation.customer_id != customer.id:
                # 客户关联必须先于消息落库，避免产生没有正式档案的孤立消息。
                conversation.customer_id = customer.id
                await self._conversations.save(conversation)
        if not await self._messages.record_incoming(conversation.id, message):
            return
        if message.origin is MessageOrigin.SERVICER:
            # 一旦人工客服发言，立即锁定人工模式，防止客人下一条消息又触发机器人。
            if conversation.mode is not ConversationMode.HUMAN_ACTIVE:
                await self._switch_to_human(
                    conversation,
                    "servicer_reply",
                )
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
        if not is_homestay_related(message.content):
            await self._send_guest_reply(
                conversation,
                (
                    "我主要协助民宿入住或武汉旅行相关问题，"
                    "这类问题暂时无法回答。"
                ),
            )
            return

        if self._defer_model and self._jobs is not None:
            await self._stage_fast_ack(conversation, message)
            return

        await self._process_model_reply(conversation, message)

    async def process_recorded_message(self, message: IncomingMessage) -> None:
        """处理已完成入站提交的消息，供后台最终回复任务调用。"""
        conversation = await self._conversations.get_or_create(message)
        await self._process_model_reply(conversation, message)

    async def _stage_fast_ack(
        self,
        conversation: Conversation,
        message: IncomingMessage,
    ) -> None:
        """登记模型快速安抚和最终处理任务，再提交让发送 worker 立即可见。"""
        jobs = self._jobs
        if jobs is None:
            return
        ack = await self._assistant.respond_ack(
            guest_identifier=message.external_userid,
            language=conversation.language,
            question=message.content,
        )
        await self._send_guest_reply(conversation, ack, message_type="ack")
        await jobs.enqueue(
            "wecom_process_message",
            {
                "msgid": message.msgid,
                "open_kfid": message.open_kfid,
                "external_userid": message.external_userid,
                "origin": message.origin.value,
                "msgtype": message.msgtype,
                "content": message.content,
                "sent_at": message.sent_at.isoformat(),
            },
            dedupe_key=f"final:{message.msgid}",
        )
        if self._commit_boundary is not None:
            await self._commit_boundary()

    async def _process_model_reply(
        self,
        conversation: Conversation,
        message: IncomingMessage,
    ) -> None:
        """执行耗时模型和业务副作用；快速安抚已在前一事务发送。"""

        try:
            model_context = None
            if self._customer_context is not None and conversation.customer_id is not None:
                model_context = await self._customer_context.load_model_context(
                    conversation.customer_id
                )
            decision = await self._assistant.respond(
                guest_identifier=message.external_userid,
                language=conversation.language,
                messages=await self._messages.build_context(conversation.id, limit=3),
                customer_context=model_context,
            )
        except TourismSearchError as error:
            await self._escalate_tourism_failure(conversation, message, error)
            return
        except AssistantUnavailableError:
            await self._escalate_assistant_failure(conversation, message)
            return
        reply_text = self._warm_guest_reply(
            self._limit_assistant_reply(decision.reply_text)
        )
        await self._send_guest_reply(conversation, reply_text)
        await self._track_frequent_faq(message, decision)
        await self._record_task_suggestion(conversation, message, decision)
        local_handoff_reason = determine_handoff_reason(message.content)
        if local_handoff_reason or decision.handoff_reason:
            reason = local_handoff_reason or decision.handoff_reason
            await self._activate_human(
                conversation,
                message,
                f"YuMi 接管：{reason}",
                audit_reason=reason,
            )
            return
        if decision.intent == "booking_confirmed":
            await self._create_pending_approval(conversation, message, decision)
            return
        # 未确认的普通交易事实只提醒员工核实，机器人继续承接后续对话。
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

    async def _record_task_suggestion(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        decision: AssistantDecision,
    ) -> None:
        """客人回复成功后幂等保存待确认任务，失败不回滚可见回复。"""
        suggestion: TaskSuggestion | None = decision.task_suggestion
        if (
            self._business_tasks is None
            or conversation.customer_id is None
            or suggestion is None
        ):
            return
        try:
            task = await self._business_tasks.record_ai_suggestion(
                customer_id=conversation.customer_id,
                source_message_id=message.msgid,
                task_type=suggestion.task_type,
                description=suggestion.description,
                property_id=suggestion.property_id,
                service_date=suggestion.service_date,
            )
            await self._notify_employee(
                conversation,
                message,
                f"新任务待确认：ID {task.id}，类型 {task.task_type.value}",
            )
        except Exception as error:
            # 任务记录是回复后的副作用；日志只保留异常类型，不复制聊天正文。
            logger.warning(
                "AI 待确认任务记录失败，已保留客人回复：error_type=%s",
                type(error).__name__,
            )
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
        await self._switch_to_human(
            conversation,
            "booking_approval_created",
        )
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
        *,
        audit_reason: str | None = None,
    ) -> None:
        """切换人工模式并发送内部原因摘要。"""
        await self._switch_to_human(
            conversation,
            audit_reason or reason,
        )
        await self._notify_employee(conversation, message, reason)

    async def _switch_to_human(
        self,
        conversation: Conversation,
        reason: str,
    ) -> None:
        """保存人工模式，并记录不含聊天正文的接管审计。"""
        conversation.mode = ConversationMode.HUMAN_ACTIVE
        await self._conversations.save(conversation)
        if self._audit_events is not None:
            await self._audit_events.record_handoff(
                conversation_id=conversation.id,
                customer_id=conversation.customer_id,
                reason=reason,
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
        self,
        conversation: Conversation,
        content: str,
        *,
        message_type: str = "text",
    ) -> None:
        """发送并持久化机器人文本。"""
        message_id = await self._wecom.send_text(
            conversation.open_kfid,
            conversation.external_userid,
            content,
        )
        if message_type == "text":
            await self._messages.record_bot(conversation.id, message_id, content)
        else:
            await self._messages.record_bot(
                conversation.id,
                message_id,
                content,
                message_type=message_type,
            )

    @staticmethod
    def _warm_guest_reply(content: str) -> str:
        """统一把内部确认术语改成温暖、诚实且客人易懂的表达。"""
        replacements = {
            (
                "该需求会提交给工作人员确认并安排，最终是否安排成功需以员工确认为准，"
                "不便之处敬请谅解。"
            ): "我已经帮您记下啦，会尽快为您安排，稍后给您反馈。",
            "以员工确认为准": "以最终安排结果为准",
            "需员工确认": "我们会尽快帮您核实",
            "由工作人员进一步确认": "我们会尽快为您核实",
            "请由工作人员进一步确认": "我会尽快为您核实",
            "建议到店前由工作人员进一步确认": "建议到店前我再为您核实",
            "到店前再请工作人员确认": "到店前我再帮您核实",
        }
        for source, target in replacements.items():
            content = content.replace(source, target)
        return content

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
        await self._activate_human(
            conversation,
            message,
            f"紧急事件：{emergency.category}",
            audit_reason=f"emergency:{emergency.category}",
        )

    async def _escalate_regular(
        self, conversation: Conversation, message: IncomingMessage
    ) -> None:
        """对媒体、投诉和客人主动要求人工等情况执行普通接管。"""
        reply = (
            "Thanks for letting us know. I’ve asked our host team to help you shortly."
            if conversation.language is Language.EN
            else "收到啦，我已经帮您联系管家，很快会来协助您。"
        )
        await self._send_guest_reply(conversation, reply)
        await self._activate_human(
            conversation,
            message,
            "普通人工接管",
            audit_reason="manual_request_or_media",
        )

    async def _escalate_tourism_failure(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        error: TourismSearchError,
    ) -> None:
        """明确告知联网失败，再切人工并通知值班员工。"""
        reply = (
            "Sorry, I couldn’t finish the live search just now. "
            "I’ve noted it and will continue checking for you."
            if conversation.language is Language.EN
            else "抱歉，实时信息刚才没能查完整。我已经帮您记下，会继续为您查清楚。"
        )
        await self._send_guest_reply(conversation, reply)
        await self._activate_human(
            conversation,
            message,
            f"旅游联网失败：{error.status}",
            audit_reason=f"tourism_failure:{error.status}",
        )

    async def _escalate_assistant_failure(
        self,
        conversation: Conversation,
        message: IncomingMessage,
    ) -> None:
        """告知普通模型暂不可用，再切人工并通知值班员工。"""
        reply = (
            "Sorry, I couldn’t finish checking this just now. "
            "I’ve noted it and will continue helping you."
            if conversation.language is Language.EN
            else "抱歉，刚才查询没有顺利完成。我已经帮您记下，会继续为您处理。"
        )
        await self._send_guest_reply(conversation, reply)
        await self._activate_human(
            conversation,
            message,
            "模型服务暂时不可用",
            audit_reason="assistant_unavailable",
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
