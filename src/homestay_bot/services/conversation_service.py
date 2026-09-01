import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from pydantic import ValidationError

from homestay_bot.domain.enums import (
    BusinessTaskType,
    ConversationMode,
    JobStatus,
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
    facility_fault_exclusion,
    has_facility_fault_signal,
    is_booking_action_request,
    is_homestay_related,
    is_service_request,
)
from homestay_bot.services.answer_policy import (
    handoff_reason as determine_handoff_reason,
)
from homestay_bot.services.context_retention import CustomerModelContext
from homestay_bot.services.emergency_service import (
    EmergencyClassification,
    EmergencyService,
)
from homestay_bot.services.guest_reply_policy import (
    prepare_facility_issue_reply,
    prepare_guest_reply,
)
from homestay_bot.services.message_service import GuestMessageBatch, IncomingMessage
from homestay_bot.worker import DeferredRetryJobError

_MAX_ASSISTANT_REPLY_CHARACTERS = 1500
_GUEST_MESSAGE_DEBOUNCE_SECONDS = 3
logger = logging.getLogger(__name__)


class ConversationRepository(Protocol):
    """定义会话编排所需的持久化接口。"""

    async def get_or_create(self, message: IncomingMessage) -> Conversation:
        """查找或创建客人会话。"""

    async def save(self, conversation: Conversation) -> None:
        """保存会话状态。"""

    async def lock_activity(self, conversation_id: int) -> None:
        """锁定会话行，串行化入站活动与静默任务消费。"""


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
        self,
        conversation_id: int,
        limit: int = 20,
        through_external_message_id: str | None = None,
        *,
        merged_guest_content: str | None = None,
        merged_guest_count: int = 1,
    ) -> list[dict[str, str]]:
        """返回有限的客人与机器人历史上下文。"""

    async def build_guest_batch(
        self,
        conversation_id: int,
        through_external_message_id: str,
        *,
        quiet_window_seconds: int = 3,
        max_messages: int = 10,
        max_characters: int = 2000,
    ) -> GuestMessageBatch:
        """返回来源边界前、静默窗口内的连续客人文本。"""

    async def has_newer_guest_message(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源消息后是否已经有更新的客人问题。"""

    async def has_newer_conversation_activity(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源消息后是否出现客人或员工的新活动。"""


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
        self,
        open_kfid: str,
        external_userid: str,
        content: str,
        *,
        message_type: str = "text",
    ) -> str | None:
        """发送客人文本并返回消息编号；重复阶段返回空值。"""

    async def send_internal_text(
        self,
        *,
        agent_id: int,
        employee_userids: list[str],
        content: str,
    ) -> None:
        """通知值班员工处理人工会话。"""


class WeComIdentityPort(Protocol):
    """定义员工通知所需的企业微信展示名称查询接口。"""

    async def get_kf_account_name(self, open_kfid: str) -> str | None:
        """返回微信客服账号名称。"""

    async def get_kf_customer_name(
        self,
        open_kfid: str,
        external_userid: str,
    ) -> str | None:
        """返回微信客服客人昵称。"""


class CustomerNotificationPort(Protocol):
    """定义员工通知所需的 CRM 客人备注接口。"""

    async def get_customer_notification_note(
        self,
        customer_id: int,
    ) -> str | None:
        """优先返回自动入住备注，再回退员工手写备注。"""


class PendingApprovalPort(Protocol):
    """定义客人确认资料后创建待审批单的唯一入口。"""

    async def create_pending(
        self,
        conversation_id: int,
        request: BookingRequest,
        *,
        source_message_id: str | None = None,
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

    async def load_model_context(
        self, customer_id: int, *, query: str = ""
    ) -> CustomerModelContext:
        """按当前问题返回不含原文和敏感字段的客户摘要。"""


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
        available_at: datetime | None = None,
        dedupe_key: str | None = None,
    ) -> object:
        """登记可恢复的后台任务。"""

    async def status_for_dedupe_key(self, dedupe_key: str) -> JobStatus | None:
        """返回指定任务状态，供最终阶段确认快速安抚已经投递。"""


@dataclass(frozen=True)
class GuestReplyReceipt:
    """记录客人出口实际正文和发送端返回的消息编号。"""

    content: str
    message_id: str | None


class ComplaintClassifierPort(Protocol):
    """定义本地客诉识别和固定安抚接口。"""

    def classify(self, text: str) -> Any:
        """识别客诉风险。"""

    @staticmethod
    def guest_acknowledgement() -> str:
        """返回客诉固定安抚。"""


class ComplaintReviewPort(Protocol):
    """定义客诉记录所需的最小接口。"""

    async def create_or_get(
        self,
        *,
        conversation_id: int,
        source_message_id: str,
        reason: str,
        risk_level: str,
    ) -> Any:
        """按来源消息幂等创建客诉记录。"""


class ConversationService:
    """按来源、会话状态和风险规则编排机器人与人工处理。"""

    _EMPLOYEE_NOTIFICATION_MAX_BYTES = 2048

    _handoff_pattern = re.compile(
        r"人工客服|转人工|找人工|工作人员接待|"
        r"human agent|live agent|staff member",
        re.IGNORECASE,
    )

    @staticmethod
    def _employee_notification_label(
        value: object,
        *,
        fallback: str | None = None,
        max_bytes: int = 600,
    ) -> str | None:
        """把员工通知中的外部展示值压成单行，并按 UTF-8 字节安全限长。"""

        cleaned = " ".join(str(value or "").split())[:200].strip()
        cleaned = ConversationService._truncate_utf8(cleaned, max_bytes=max_bytes)
        return cleaned or fallback

    @staticmethod
    def _truncate_utf8(value: str, *, max_bytes: int) -> str:
        """在不切断多字节字符的前提下，把文本限制在指定 UTF-8 字节数内。"""

        encoded = value.encode("utf-8")
        if len(encoded) <= max_bytes:
            return value
        return encoded[:max_bytes].decode("utf-8", errors="ignore")

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
        identity_resolver: WeComIdentityPort | None = None,
        customer_notification: CustomerNotificationPort | None = None,
        complaint_service: ComplaintClassifierPort | None = None,
        complaint_reviews: ComplaintReviewPort | None = None,
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
        self._identity_resolver = identity_resolver
        self._customer_notification = customer_notification
        self._complaint_service = complaint_service
        self._complaint_reviews = complaint_reviews
        self._defer_model = defer_model
        self._commit_boundary = commit_boundary

    async def handle_message(self, message: IncomingMessage) -> None:
        """处理单条已去重消息，确保人工回复不会形成机器人回环。"""
        conversation = await self._conversations.get_or_create(message)
        await self._conversations.lock_activity(conversation.id)
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

        if self._complaint_service is not None:
            classification = self._complaint_service.classify(message.content)
            if classification.is_complaint:
                await self._enter_complaint_mode(
                    conversation,
                    message,
                    classification,
                )
                return

        # 人工接管只拦截新的高风险事项；客诉处理期间出现房态、旅游等
        # 独立低风险问题时继续由机器人回答，避免一次投诉永久阻塞客服。
        if (
            conversation.mode is ConversationMode.HUMAN_ACTIVE
            and self._determine_handoff_reason(message.content) is not None
        ):
            await self._send_guest_reply(
                conversation,
                "我已收到您的诉求。",
                requires_human=True,
                high_risk=True,
            )
            await self._notify_employee(
                conversation,
                message,
                "人工接待会话收到新的高风险消息",
            )
            return

        if message.msgtype != "text" or self._handoff_pattern.search(message.content):
            await self._escalate_regular(conversation, message)
            return
        if self._defer_model and self._jobs is not None:
            await self._enqueue_debounce(message)
            return

        if not is_homestay_related(message.content):
            await self._send_unrelated_reply(conversation)
            return

        await self._process_model_reply(conversation, message)

    async def process_debounced_message(self, message: IncomingMessage) -> None:
        """静默窗口结束后合并当前批次，再决定安抚和最终处理。"""
        conversation = await self._conversations.get_or_create(message)
        await self._conversations.lock_activity(conversation.id)
        if await self._messages.has_newer_conversation_activity(
            conversation.id,
            message.msgid,
        ):
            return
        batch = await self._messages.build_guest_batch(
            conversation.id,
            message.msgid,
            quiet_window_seconds=_GUEST_MESSAGE_DEBOUNCE_SECONDS,
            max_messages=10,
            max_characters=2000,
        )
        if not batch.content or batch.message_count < 1:
            return
        merged_message = replace(
            message,
            content=batch.content,
            metadata={
                **(message.metadata or {}),
                "merged_guest_count": str(batch.message_count),
            },
        )
        rule_contents = self._policy_questions(merged_message.content)
        emergency = EmergencyClassification(False)
        for rule_content in rule_contents:
            emergency = self._emergency.classify(rule_content)
            if emergency.is_emergency:
                break
        if emergency.is_emergency:
            await self._escalate_emergency(conversation, merged_message, emergency)
            return
        if self._complaint_service is not None:
            classification = self._complaint_service.classify(rule_contents[0])
            for rule_content in rule_contents[1:]:
                if classification.is_complaint:
                    break
                classification = self._complaint_service.classify(rule_content)
            if classification.is_complaint:
                await self._enter_complaint_mode(
                    conversation,
                    merged_message,
                    classification,
                )
                return
        handoff_reason = self._determine_handoff_reason(merged_message.content)
        if (
            conversation.mode is ConversationMode.HUMAN_ACTIVE
            and handoff_reason is not None
        ):
            await self._send_guest_reply(
                conversation,
                "我已收到您的诉求。",
                requires_human=True,
                high_risk=True,
            )
            await self._notify_employee(
                conversation,
                merged_message,
                "人工接待会话收到新的高风险消息",
            )
            return
        if any(self._handoff_pattern.search(value) for value in rule_contents):
            await self._escalate_regular(conversation, merged_message)
            return
        if not is_homestay_related(merged_message.content):
            await self._send_unrelated_reply(conversation)
            return
        await self._stage_fast_ack(conversation, merged_message)

    async def process_recorded_message(self, message: IncomingMessage) -> None:
        """处理已完成入站提交的消息，供后台最终回复任务调用。"""
        conversation = await self._conversations.get_or_create(message)
        message = await self._resolve_fast_ack_delivery(message)
        # 人工接管期间只丢弃当前高风险事项；房态、旅游等独立问题仍应回复，
        # 同时保留人工模式，让正在处理的客诉继续由管家跟进。
        if (
            conversation.mode is ConversationMode.HUMAN_ACTIVE
            and self._determine_handoff_reason(message.content) is not None
        ):
            return
        if await self._messages.has_newer_conversation_activity(
            conversation.id,
            message.msgid,
        ):
            return
        await self._process_model_reply(
            conversation,
            message,
            discard_if_stale=True,
        )

    async def _resolve_fast_ack_delivery(
        self,
        message: IncomingMessage,
    ) -> IncomingMessage:
        """确认快速安抚已被企业微信接受；仍在发送时延迟最终任务。"""

        metadata = message.metadata or {}
        outbox_id = str(metadata.get("fast_ack_outbox_id", ""))
        if not outbox_id or self._jobs is None:
            return message
        status = await self._jobs.status_for_dedupe_key(outbox_id)
        if status in {JobStatus.PENDING, JobStatus.RUNNING}:
            raise DeferredRetryJobError("快速安抚仍在发送中")
        if status is not JobStatus.COMPLETED:
            # 未找到或发送失败时，最终回复必须承担客人兜底，不能按摘要抑制。
            mutable_metadata = dict(metadata)
            mutable_metadata.pop("fast_ack_sha256", None)
            return replace(message, metadata=mutable_metadata)
        return message

    async def _enter_complaint_mode(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        classification: Any,
    ) -> None:
        """切换人工并登记客诉分析任务，客人只收到固定安抚。"""
        await self._switch_to_human(
            conversation,
            f"complaint:{classification.reason or 'complaint'}",
        )
        if self._complaint_service is not None:
            await self._send_guest_reply(
                conversation,
                self._complaint_service.guest_acknowledgement(),
                message_type="complaint_ack",
                requires_human=True,
                high_risk=True,
            )
        if self._complaint_reviews is None:
            return
        review = await self._complaint_reviews.create_or_get(
            conversation_id=conversation.id,
            source_message_id=message.msgid,
            reason=classification.reason or "complaint",
            risk_level=classification.risk_level,
        )
        if self._jobs is not None:
            await self._jobs.enqueue(
                "complaint_review_generate",
                {
                    "review_id": int(review.id),
                    "conversation_id": conversation.id,
                    "source_message_id": message.msgid,
                },
                dedupe_key=f"complaint:{message.msgid}",
            )

    async def _handle_facility_issue(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        reply_text: str,
    ) -> None:
        """先登记维修任务和员工通知，再发送经过安全清洗的模型建议。"""
        if self._business_tasks is None or conversation.customer_id is None:
            # 生产装配必须同时提供正式客户和任务仓储；缺失时回滚入站并由 worker 重试。
            raise RuntimeError("设施故障人工任务依赖未配置")
        task = await self._business_tasks.record_ai_suggestion(
            customer_id=conversation.customer_id,
            source_message_id=message.msgid,
            task_type=BusinessTaskType.MAINTENANCE,
            description="客人反馈民宿设施故障，待人工处理",
        )
        await self._notify_employee(
            conversation,
            message,
            f"新任务待确认：ID {task.id}，类型 {task.task_type.value}",
        )
        cleaned_reply = ""
        if reply_text.strip():
            # 空串是模型异常或低置信的显式信号，必须保留给回复策略触发安全降级。
            cleaned_reply = self._clean_guest_reply_topics(
                self._limit_assistant_reply(reply_text),
                question=message.content,
            )
        reply = prepare_facility_issue_reply(cleaned_reply, conversation.language)
        await self._send_prepared_guest_reply(conversation, reply)

    async def _stage_fast_ack(
        self,
        conversation: Conversation,
        message: IncomingMessage,
    ) -> None:
        """按需发送快速安抚并登记最终处理任务，再提交让 worker 立即可见。"""
        jobs = self._jobs
        if jobs is None:
            return
        fast_ack_sha256: str | None = None
        if (
            not has_facility_fault_signal(message.content)
            and self._should_send_fast_ack(message.content)
        ):
            ack = await self._assistant.respond_ack(
                guest_identifier=message.external_userid,
                language=conversation.language,
                question=message.content,
            )
            sent_ack = await self._send_guest_reply(
                conversation,
                ack,
                message_type="ack",
                requires_human=True,
            )
            # 最终阶段只携带摘要，避免在任务载荷中复制一份安抚正文。
            fast_ack_sha256 = hashlib.sha256(
                sent_ack.content.encode("utf-8")
            ).hexdigest()
        payload: dict[str, object] = {
            "phase": "final",
            "msgid": message.msgid,
            "open_kfid": message.open_kfid,
            "external_userid": message.external_userid,
            "origin": message.origin.value,
            "msgtype": message.msgtype,
            "content": message.content,
            "sent_at": message.sent_at.isoformat(),
        }
        merged_guest_count = str(
            (message.metadata or {}).get("merged_guest_count", "")
        )
        if merged_guest_count.isdigit() and int(merged_guest_count) > 1:
            payload["merged_guest_count"] = int(merged_guest_count)
        if fast_ack_sha256 is not None and sent_ack.message_id is not None:
            payload["fast_ack_sha256"] = fast_ack_sha256
            if sent_ack.message_id and sent_ack.message_id.startswith("outbox:"):
                payload["fast_ack_outbox_id"] = sent_ack.message_id
        await jobs.enqueue(
            "wecom_process_message",
            payload,
            dedupe_key=f"final:{message.msgid}",
        )
        if self._commit_boundary is not None:
            await self._commit_boundary()

    async def _enqueue_debounce(self, message: IncomingMessage) -> None:
        """为普通客人文本登记三秒后的静默检查，不提前生成安抚。"""
        jobs = self._jobs
        if jobs is None:
            return
        await jobs.enqueue(
            "wecom_process_message",
            {
                "phase": "debounce",
                "msgid": message.msgid,
                "open_kfid": message.open_kfid,
                "external_userid": message.external_userid,
                "origin": message.origin.value,
                "msgtype": message.msgtype,
                "content": message.content,
                "sent_at": message.sent_at.isoformat(),
            },
            available_at=datetime.now(UTC)
            + timedelta(seconds=_GUEST_MESSAGE_DEBOUNCE_SECONDS),
            dedupe_key=f"debounce:{message.msgid}",
        )
        if self._commit_boundary is not None:
            await self._commit_boundary()

    async def _send_unrelated_reply(self, conversation: Conversation) -> None:
        """发送固定的非民宿问题边界说明。"""
        await self._send_guest_reply(
            conversation,
            "我主要协助民宿入住或武汉旅行相关问题，这类问题暂时无法回答。",
        )

    @staticmethod
    def _should_send_fast_ack(question: str) -> bool:
        """只为需要后台处理的服务请求发送安抚，普通查询直接等待最终答案。"""
        supply = r"矿泉水|饮用水|纸巾|被子|枕头|床单|毛巾|拖鞋|牙刷|耗材"
        patterns = (
            rf"(?:补|送|拿|更换|换|加).{{0,8}}(?:{supply})",
            rf"(?:{supply}).{{0,8}}(?:补|送|拿|更换|换|加)",
            r"保洁|打扫|收房|收垃圾|调麻将机|生日布置|求婚布置",
            r"提前入住|延迟退房|特殊服务",
            r"help.{0,20}(?:water|towel|blanket)|housekeeping",
        )
        return any(
            re.search(pattern, policy_question, re.IGNORECASE)
            for policy_question in ConversationService._policy_questions(question)
            for pattern in patterns
        )

    @staticmethod
    def _policy_questions(question: str) -> tuple[str, ...]:
        """生成确定性规则候选文本，不改变交给模型和员工的原始合并正文。"""
        flattened = " ".join(question.split())
        compact = re.sub(r"\s+", "", question)
        return tuple(dict.fromkeys((question, flattened, compact)))

    @classmethod
    def _determine_handoff_reason(cls, question: str) -> str | None:
        """在原文及跨消息空白归一化文本中识别人工接管原因。"""
        return next(
            (
                reason
                for policy_question in cls._policy_questions(question)
                if (reason := determine_handoff_reason(policy_question)) is not None
            ),
            None,
        )

    async def _process_model_reply(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        *,
        discard_if_stale: bool = False,
    ) -> None:
        """执行耗时模型和业务副作用；快速安抚已在前一事务发送。"""

        try:
            model_context = None
            if self._customer_context is not None and conversation.customer_id is not None:
                model_context = await self._customer_context.load_model_context(
                    conversation.customer_id,
                    query=message.content,
                )
            merged_guest_count_text = str(
                (message.metadata or {}).get("merged_guest_count", "1")
            )
            merged_guest_count = (
                int(merged_guest_count_text)
                if merged_guest_count_text.isdigit()
                else 1
            )
            decision = await self._assistant.respond(
                guest_identifier=message.external_userid,
                language=conversation.language,
                messages=await self._messages.build_context(
                    conversation.id,
                    limit=3,
                    through_external_message_id=message.msgid,
                    merged_guest_content=(
                        message.content if merged_guest_count > 1 else None
                    ),
                    merged_guest_count=merged_guest_count,
                ),
                customer_context=model_context,
            )
        except TourismSearchError as error:
            if discard_if_stale and await self._discard_stale_final(
                conversation,
                message,
            ):
                return
            await self._escalate_tourism_failure(conversation, message, error)
            return
        except AssistantUnavailableError:
            if discard_if_stale and await self._discard_stale_final(
                conversation,
                message,
            ):
                return
            if (
                self._determine_handoff_reason(message.content) is None
                and self._facility_reply_text(message.content, None) is not None
            ):
                await self._handle_facility_issue(
                    conversation,
                    message,
                    "",
                )
                return
            await self._escalate_assistant_failure(conversation, message)
            return
        if discard_if_stale and await self._discard_stale_final(
            conversation,
            message,
        ):
            return
        local_handoff_reason = self._determine_handoff_reason(message.content)
        facility_reply_text = self._facility_reply_text(message.content, decision)
        if (
            local_handoff_reason is None
            and decision.handoff_reason is None
            and facility_reply_text is not None
        ):
            await self._handle_facility_issue(
                conversation,
                message,
                facility_reply_text,
            )
            return
        service_requested = is_service_request(message.content)
        booking_action_requested = is_booking_action_request(message.content)
        requires_human = bool(
            local_handoff_reason
            or decision.handoff_reason
            or decision.staff_confirmation_required
            or (decision.task_suggestion is not None and service_requested)
            or (decision.intent == "booking_confirmed" and booking_action_requested)
            or self._should_send_fast_ack(message.content)
        )
        reply_text = self._clean_guest_reply_topics(
            self._limit_assistant_reply(decision.reply_text),
            question=message.content,
        )
        prepared_reply = prepare_guest_reply(
            reply_text,
            language=conversation.language,
            requires_human=requires_human,
            question=message.content,
            high_risk=bool(local_handoff_reason or decision.handoff_reason),
        )
        fast_ack_sha256 = str((message.metadata or {}).get("fast_ack_sha256", ""))
        prepared_sha256 = hashlib.sha256(prepared_reply.encode("utf-8")).hexdigest()
        # 快速安抚已经包含全部最终内容时不重复发送；后续业务副作用仍照常执行。
        if fast_ack_sha256 != prepared_sha256:
            await self._send_prepared_guest_reply(
                conversation,
                prepared_reply,
            )
        await self._track_frequent_faq(message, decision)
        await self._record_task_suggestion(conversation, message, decision)
        if local_handoff_reason or decision.handoff_reason:
            reason = local_handoff_reason or decision.handoff_reason
            await self._activate_human(
                conversation,
                message,
                f"YuMi 接管：{reason}",
                audit_reason=reason,
            )
            return

        if decision.intent == "booking_confirmed" and booking_action_requested:
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

    @staticmethod
    def _facility_reply_text(
        question: str,
        decision: AssistantDecision | None,
    ) -> str | None:
        """返回可用于民宿设施故障的模型正文，异常时用空串触发降级。"""
        if facility_fault_exclusion(question) is not None:
            return None
        issue = decision.facility_issue if decision is not None else None
        if issue is not None:
            if issue.scope in {"private", "external"}:
                return None
            # 结构化归属负责开放语义；低置信和不确定归属只使用本地通用建议。
            if issue.scope == "uncertain" or decision is None or decision.confidence < 0.7:
                return ""
            return decision.reply_text
        if has_facility_fault_signal(question):
            # 模型字段缺失或模型不可用时，明确本地信号仍进入确定性兜底。
            return ""
        return None

    async def _discard_stale_final(
        self,
        conversation: Conversation,
        message: IncomingMessage,
    ) -> bool:
        """模型完成后锁定会话并复查活动，锁由外层事务保持到提交。"""
        await self._conversations.lock_activity(conversation.id)
        return await self._messages.has_newer_conversation_activity(
            conversation.id,
            message.msgid,
        )

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
            or not is_service_request(message.content)
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
        if not is_booking_action_request(message.content):
            # 业务层再次校验本轮确认语义，隔离错误模型或其他 Assistant 实现。
            return
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

        approval = await self._approvals.create_pending(
            conversation.id,
            request,
            source_message_id=message.msgid,
        )
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
        requires_human: bool = False,
        question: str = "",
        high_risk: bool = False,
    ) -> GuestReplyReceipt:
        """经过统一风格与安全策略后发送，并返回真实客人可见正文。"""
        content = prepare_guest_reply(
            content,
            language=conversation.language,
            requires_human=requires_human,
            question=question,
            high_risk=high_risk,
        )
        return await self._send_prepared_guest_reply(
            conversation,
            content,
            message_type=message_type,
        )

    async def _send_prepared_guest_reply(
        self,
        conversation: Conversation,
        content: str,
        *,
        message_type: str = "text",
    ) -> GuestReplyReceipt:
        """发送已经过统一客人侧策略处理的文本，并记录真实消息编号。"""

        message_id = await self._wecom.send_text(
            conversation.open_kfid,
            conversation.external_userid,
            content,
            message_type=message_type,
        )
        if message_id is None or message_id.startswith("outbox:"):
            return GuestReplyReceipt(content=content, message_id=message_id)
        if message_type == "text":
            await self._messages.record_bot(conversation.id, message_id, content)
        else:
            await self._messages.record_bot(
                conversation.id,
                message_id,
                content,
                message_type=message_type,
            )
        return GuestReplyReceipt(content=content, message_id=message_id)

    @staticmethod
    def _clean_guest_reply_topics(content: str, *, question: str = "") -> str:
        """清理与当前问题无关的旧话题；风格和安全由统一策略负责。"""
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
        # 只替换内部角色短语，保留退款对象、核实依据等业务事实。
        content = re.sub(
            r"(?:跟|与)(?:工作人员|员工)确认",
            "进一步核实",
            content,
        )
        content = re.sub(
            r"(?:需|需要)(?:工作人员|员工)(?:进一步|再)?确认",
            "需要进一步核实",
            content,
        )
        content = re.sub(
            r"(?:请|由)(?:工作人员|员工)(?:进一步|再)?确认",
            "我会尽快为您核实",
            content,
        )
        content = re.sub(
            r"会安排(?:工作人员|员工)[^。！？，]*?给您",
            "已联系管家，会尽快为您",
            content,
        )
        # 服务安排只向客人承诺已记录和持续跟进，不展示内部派送动作。
        content = re.sub(
            r"(?:马上|尽快)?让(?:工作人员|员工)(?:给您)?(?:送|补)[^，。！？]*",
            "已联系管家，会尽快为您补上",
            content,
        )
        content = re.sub(
            r"已提交(?:给)?(?:工作人员|员工)确认",
            "已联系管家核实",
            content,
        )
        content = content.replace("确认后会尽快给您回复", "有结果后马上告诉您")
        if question:
            # 模型偶尔会把上一轮任务或客诉一起写进本轮回复；按当前问题
            # 删除未被请求的服务句，避免退款、补水等承诺互相串线。
            requested_topics = {
                topic
                for topic in (
                    "退款",
                    "退钱",
                    "退费",
                    "矿泉水",
                    "补水",
                    "纸巾",
                    "被子",
                    "床单",
                    "枕头",
                    "麻将",
                    "维修",
                    "保洁",
                    "提前入住",
                    "延迟退房",
                )
                if topic in question
            }
            if {"矿泉水", "补水"} & requested_topics:
                # “补水”和“矿泉水”在客人表达中是同一项服务，避免清理时
                # 把正常的补水确认句误删为空回复。
                requested_topics.update({"矿泉水", "补水"})
            removable_topics = (
                "退款|退钱|退费|矿泉水|补水|纸巾|被子|床单|枕头|麻将|维修|"
                "保洁|提前入住|延迟退房"
            )
            sentences = re.split(r"(?<=[。！？；;])", content)
            filtered_sentences: list[str] = []
            for sentence in sentences:
                mentioned = set(re.findall(removable_topics, sentence))
                if mentioned and not (mentioned & requested_topics):
                    continue
                filtered_sentences.append(sentence)
            content = "".join(filtered_sentences).strip()
        return content or "我已收到您的诉求。"

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
        await self._send_guest_reply(
            conversation,
            reply,
            requires_human=True,
            high_risk=True,
        )
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
            "Thanks for letting us know."
            if conversation.language is Language.EN
            else "我已收到您的诉求。"
        )
        await self._send_guest_reply(
            conversation,
            reply,
            requires_human=True,
            high_risk=True,
        )
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
            "Sorry, I couldn’t finish the live search just now."
            if conversation.language is Language.EN
            else "抱歉，实时信息刚才没能查完整。"
        )
        await self._send_guest_reply(
            conversation,
            reply,
            requires_human=True,
        )
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
            "Sorry, I couldn’t finish checking this just now."
            if conversation.language is Language.EN
            else "抱歉，刚才查询没有顺利完成。"
        )
        await self._send_guest_reply(
            conversation,
            reply,
            requires_human=True,
        )
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
        customer_service_name = "微信客服"
        guest_name = "客人"
        if self._identity_resolver is not None:
            try:
                customer_service_name = (
                    await self._identity_resolver.get_kf_account_name(
                        conversation.open_kfid
                    )
                    or customer_service_name
                )
                guest_name = (
                    await self._identity_resolver.get_kf_customer_name(
                        conversation.open_kfid,
                        conversation.external_userid,
                    )
                    or guest_name
                )
            except Exception as error:
                # 名称接口不可用不应阻塞任务通知，且不把 UID 回退给员工端。
                logger.warning(
                    "企业微信展示名称读取失败，使用友好名称：error_type=%s",
                    type(error).__name__,
                )
        customer_service_name = (
            self._employee_notification_label(
                customer_service_name,
                fallback="微信客服",
                max_bytes=240,
            )
            or "微信客服"
        )
        guest_name = (
            self._employee_notification_label(
                guest_name,
                fallback="客人",
            )
            or "客人"
        )
        # 员工端优先看到 CRM 备注；没有任何备注时再显示企业微信客人名称。
        customer_note = None
        if (
            self._customer_notification is not None
            and conversation.customer_id is not None
        ):
            try:
                customer_note = (
                    await self._customer_notification.get_customer_notification_note(
                        conversation.customer_id
                    )
                )
            except Exception as error:
                # CRM 备注查询失败不应阻塞人工通知，继续使用客人名称兜底。
                logger.warning(
                    "客户通知备注读取失败，使用客人名称兜底：error_type=%s",
                    type(error).__name__,
                )
        customer_note = self._employee_notification_label(customer_note)
        display_identity = (
            f"客人备注：{customer_note}"
            if customer_note
            else f"客人：{guest_name}"
        )
        reason_label = (
            self._employee_notification_label(
                reason,
                fallback="新任务待确认",
                max_bytes=300,
            )
            or "新任务待确认"
        )
        # 固定字段和客人定位信息优先保留，剩余字节全部分配给真实消息。
        notification_prefix = (
            f"{reason_label}\n客服账号：{customer_service_name}\n"
            f"{display_identity}\n消息："
        )
        remaining_bytes = max(
            0,
            self._EMPLOYEE_NOTIFICATION_MAX_BYTES
            - len(notification_prefix.encode("utf-8")),
        )
        message_text = " ".join(str(message.content or "").split())
        message_text = self._truncate_utf8(
            message_text,
            max_bytes=remaining_bytes,
        )
        await self._wecom.send_internal_text(
            agent_id=self._agent_id,
            employee_userids=self._duty_employee_userids,
            content=f"{notification_prefix}{message_text}",
        )
