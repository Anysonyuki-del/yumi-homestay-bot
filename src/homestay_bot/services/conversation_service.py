import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

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
    FacilityIssue,
    TaskSuggestion,
)
from homestay_bot.integrations.tourism import TourismSearchError, classify_tourism_query
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
    emergency_follow_up_reply,
    is_emergency_follow_up,
)
from homestay_bot.services.guest_reply_policy import (
    prepare_facility_advice_reply,
    prepare_guest_reply,
    split_guest_reply,
)
from homestay_bot.services.guest_verification import GuestVerificationService
from homestay_bot.services.message_service import (
    GuestMessageBatch,
    IncomingMessage,
    substantive_language,
)
from homestay_bot.services.reply_plan import (
    GuestActionResult,
    compose_reply_parts,
    prepare_planned_reply,
)
from homestay_bot.worker import DeferredRetryJobError


def format_employee_notification(
    *, reason: str, guest: str, room: str, link: str, original: str,
    replied: str = "尚未回复客人", account: str = "微信客服",
) -> str:
    """会话转交和自动交还共用六字段格式；先保留定位信息，再按字节平分摘要。"""
    def clip(value: str, budget: int) -> str:
        """UTF-8 截断不切断汉字，输出永不超过平台上限。"""
        return value.encode()[:budget].decode("utf-8", errors="ignore")
    prefix = (f"{clip(reason, 300)}\n客服账号：{clip(account, 240)}\n{clip(guest, 240)}"
              f"\n{clip(room, 240)}\n后台：{clip(link, 400)}")
    budget = max(0, 2048 - len(prefix.encode()) - 60) // 2
    return (f"{prefix}\n消息：{clip(original, budget)}"
            f"\n机器人已回复：{clip(replied or '尚未回复客人', budget)}")

_GUEST_MESSAGE_DEBOUNCE_SECONDS = 3
# 联网查询要等十几秒，先让客人知道在查。固定话术不调用模型，不增加等待；
# 不传问题原文，出口不会补天气开场白，也不标记人工，不会追加转人工收尾。
_LIVE_SEARCH_ACK = {
    Language.ZH: "我帮您查一下最新信息，稍等片刻。",
    Language.EN: "Let me check the latest information for you. One moment, please.",
}
# 客诉即时通知里的中文原因与风险等级，对应 ComplaintService.classify 的取值。
_COMPLAINT_REASON_LABELS = {
    "refund": "退款或赔偿诉求",
    "complaint": "平台投诉或差评",
    "agitated": "客人情绪激动",
}
_COMPLAINT_RISK_LABELS = {"critical": "严重", "high": "高", "normal": "一般"}
# 紧急类别的中文名，用于员工通知，对应 EmergencyService 的类别代码。
_EMERGENCY_CATEGORY_LABELS = {
    "fire": "火情或烟雾",
    "gas": "燃气气味",
    "electric": "漏电或触电",
    "medical": "身体不适或受伤",
    "violence": "人身安全威胁",
    "access": "无法进门",
}
logger = logging.getLogger(__name__)


class ConversationRepository(Protocol):
    """定义会话编排所需的持久化接口。"""

    async def get_or_create(self, message: IncomingMessage) -> Conversation:
        """查找或创建客人会话。"""

    async def save(self, conversation: Conversation) -> None:
        """保存会话状态。"""

    async def lock_activity(self, conversation_id: int) -> None:
        """锁定会话行，串行化入站活动与静默任务消费。"""


@runtime_checkable
class StayConfirmationPort(Protocol):
    """把确认持久化留在会话仓储，模型只提供本轮意图。"""

    async def prepare_stay_confirmation(
        self,
        conversation_id: int,
        *,
        source_message_id: str,
        today: date,
        order_id: int | None = None,
    ) -> str:
        """准备经客户归属核实的订单确认提示。"""

    async def confirm_stay(
        self, conversation_id: int, *, prompt_message_id: str, guest_message_id: str, now: datetime
    ) -> bool:
        """原子确认未变化的本次订单。"""

    async def decline_stay(self, conversation_id: int, *, prompt_message_id: str) -> None:
        """撤销仍对应当前提示的待确认记录。"""

    async def get_confirmed_stay(
        self, conversation_id: int, *, today: date, allow_history: bool = False, lock: bool = True
    ) -> dict[str, Any] | None:
        """复核客户归属和订单快照；模型前只读，发出前加锁。"""


class ConversationMessageService(Protocol):
    """定义编排层所需的消息记录接口。"""

    async def record_incoming(self, conversation_id: int, message: IncomingMessage) -> bool:
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
        stage_timing_sink: Callable[[str, int], None] | None = None,
    ) -> AssistantDecision:
        """返回经过结构校验的客服决定；stage_timing_sink 只用于耗时观测。"""

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
        stale_exempt: bool = False,
    ) -> str | None:
        """发送客人文本并返回消息编号；重复阶段返回空值。

        `stale_exempt` 表示安全与承诺类回复：排队期间来了新消息也必须送达，
        出站过时判定不能跳过它。没有排队的直接发送器可以忽略这个参数。
        """

    async def send_internal_text(
        self,
        *,
        agent_id: int,
        employee_userids: list[str],
        content: str,
    ) -> None:
        """通知值班员工处理人工会话。"""


@runtime_checkable
class ChainedGuestSenderPort(Protocol):
    """能按顺序链式发送多段客人消息的发送器（生产事务 outbox）。"""

    async def send_text_chain(
        self,
        open_kfid: str,
        external_userid: str,
        parts: list[str],
        *,
        message_type: str = "text",
        stale_exempt: bool = False,
    ) -> str | None:
        """只登记第一段，后续段在前一段发送成功后才入队，保证顺序。"""


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


@runtime_checkable
class TaskStatusPort(Protocol):
    """提供按客户归属限定的近期任务状态。"""

    async def list_recent_task_statuses(
        self, customer_id: int
    ) -> list[dict[str, str | int | None]]:
        """返回包含终态的有限记录。"""


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

    async def latest_handoff_reason(self, conversation_id: int) -> str | None:
        """返回本会话最近一次接管的原因代码；没有接管记录时返回空值。"""


class EmergencyKnowledgePort(Protocol):
    """读取已启用的审核知识，供紧急情况后续的固定处置答复使用。"""

    async def list_active(self) -> list[Any]:
        """返回全部已启用且已审核的知识条目。"""


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
    def guest_acknowledgement(language: Language = Language.ZH) -> str:
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


@dataclass
class _ReplyTiming:
    """一次正式回复的耗时与结果类别，只用于汇总日志（1.40.2），不参与分支判断。"""

    outcome: str = "replied"
    context_ms: int = 0
    respond_ms: int = 0
    stages: list[tuple[str, int]] = field(default_factory=list)

    def add_stage(self, name: str, ms: int) -> None:
        """按发生顺序记录 respond 内部的一个阶段。"""
        self.stages.append((name, ms))


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
        verification: GuestVerificationService | None = None,
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
        emergency_knowledge: EmergencyKnowledgePort | None = None,
        defer_model: bool = False,
        commit_boundary: Callable[[], Awaitable[None]] | None = None,
        savepoint_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        """注入仓储、AI、安全分类器和企业微信发送端口。"""
        self._conversations = conversations
        self._messages = messages
        self._verification = verification
        self._last_guest_reply = ""
        self._notification_task_id: int | None = None
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
        self._emergency_knowledge = emergency_knowledge
        self._defer_model = defer_model
        self._commit_boundary = commit_boundary
        self._savepoint_factory = savepoint_factory or nullcontext
        self._notification_names: tuple[str, str] | None = None

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

        detector = getattr(self._conversations, "detect_language", None)
        language = (
            await detector(conversation.id, conversation.language) if detector is not None
            else self._detect_language(message.content, conversation.language)
        )
        if language is not conversation.language:
            conversation.language = language
            await self._conversations.save(conversation)

        emergency = self._emergency.classify(message.content)
        if emergency.is_possible:
            await self._answer_possible_danger(conversation, message)
            # 已给安全提醒并通知，仍允许模型将含糊情况升级为确定危险。
            await self._process_model_reply(conversation, message)
            return
        if emergency.is_emergency:
            await self._escalate_emergency(conversation, message, emergency)
            return

        if self._verification is not None:
            verification = await self._verification.handle(conversation, message)
            if verification is not None:
                self._notification_task_id, reply = verification
                await self._send_prepared_guest_reply(conversation, reply)
                classification = (self._complaint_service.classify(message.content)
                                  if self._complaint_service is not None else None)
                reason = (f"complaint:{classification.reason}"
                          if classification and classification.is_complaint
                          else "manual_request_or_media")
                # 提交资料不覆盖已有客诉/紧急接管原因，避免巡检错误自动交还。
                if (conversation.mode is not ConversationMode.HUMAN_ACTIVE
                        or (classification and classification.is_complaint)):
                    await self._switch_to_human(conversation, reason)
                await self._notify_employee(
                    conversation, replace(message, content="核对信息请在后台任务查看"),
                    "订单待人工核实",
                )
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

        if await self._answer_emergency_follow_up(conversation, message):
            return

        # 人工接管只拦截新的高风险事项；客诉处理期间出现房态、旅游等
        # 独立低风险问题时继续由机器人回答，避免一次投诉永久阻塞客服。
        if (
            conversation.mode is ConversationMode.HUMAN_ACTIVE
            and self._determine_handoff_reason(message.content) is not None
        ):
            await self._send_guest_reply(
                conversation,
                ("I have received your request." if conversation.language is Language.EN
                 else "我已收到您的诉求。"),
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
        if emergency.is_possible:
            await self._answer_possible_danger(conversation, merged_message)
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
        if conversation.mode is ConversationMode.HUMAN_ACTIVE and handoff_reason is not None:
            await self._send_guest_reply(
                conversation,
                ("I have received your request." if conversation.language is Language.EN
                 else "我已收到您的诉求。"),
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

    async def _answer_emergency_follow_up(
        self,
        conversation: Conversation,
        message: IncomingMessage,
    ) -> bool:
        """紧急情况进行中，求助类后续消息给固定处置答复并再次通知员工；已处理时返回真。

        进行中指会话处于人工模式、最近一次接管原因是 emergency:*，员工回复或交还前一直
        有效。答复来自「紧急处置」审核知识或该类别的固定安全提示，不调用模型、不联网：
        1.40.0 之前「我们现在该怎么办」会被送去联网，回了活动推荐。独立问题照常回答。
        """
        if (
            conversation.mode is not ConversationMode.HUMAN_ACTIVE
            or self._audit_events is None
            or not is_emergency_follow_up(message.content)
        ):
            return False
        reason = await self._audit_events.latest_handoff_reason(conversation.id) or ""
        if not reason.startswith("emergency:"):
            return False
        category = reason.split(":", 1)[1]
        entries = (
            await self._emergency_knowledge.list_active()
            if self._emergency_knowledge is not None
            else []
        )
        reply = emergency_follow_up_reply(category, conversation.language, entries)
        # 固定文本已经过审核或写死在代码里，不再经过按句筛选的出口，以免处置步骤被删。
        await self._send_prepared_guest_reply(conversation, reply, stale_exempt=True)
        await self._notify_employee(
            conversation,
            message,
            f"紧急情况后续：{_EMERGENCY_CATEGORY_LABELS.get(category, '安全情况')}，客人追问",
        )
        return True

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
                self._complaint_service.guest_acknowledgement(conversation.language),
                message_type="complaint_ack",
                requires_human=True,
                high_risk=True,
            )
        # 客人刚收到「会立即联系管家」：员工通知不能等后台模型分析成功才发，模型不可用
        # 时那会是员工唯一的入口。分析卡片仍由 complaint_review_generate 成功后补发。
        await self._notify_employee(
            conversation,
            message,
            f"客诉待处理：{_COMPLAINT_REASON_LABELS.get(classification.reason, '客人投诉')}"
            f"（风险：{_COMPLAINT_RISK_LABELS.get(classification.risk_level, '高')}），"
            "分析卡片生成后另发",
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
        advice: list[str] | None,
        *,
        extra_reply: str = "",
    ) -> None:
        """先登记住宿问题任务和员工通知，再发送由建议清单组装的回复。

        `advice` 为 None 表示模型不可用或未给出清单，回复策略会使用固定兜底。
        """
        decision = AssistantDecision(
            reply_text="", language=conversation.language, intent="facility_issue", confidence=1,
            facility_issue=FacilityIssue(scope="homestay_facility"),
            task_suggestion=TaskSuggestion(
                task_type=BusinessTaskType.MAINTENANCE,
                description=message.content[:500],
            ),
        )
        action_reply = await self._record_task_suggestion(conversation, message, decision)
        # 复用请求登记的 savepoint 和失败口径，通知失败时不能沿用成功收尾。
        reply = prepare_facility_advice_reply(
            advice, conversation.language,
            action_reply=(
                None if decision.action_result and decision.action_result.notification_queued
                else action_reply
            ),
        )
        if extra_reply:
            reply = f"{extra_reply}\n\n{reply}"
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
        sent_ack: GuestReplyReceipt | None = None
        if has_facility_fault_signal(message.content):
            pass
        elif classify_tourism_query([{"role": "user", "content": message.content}]) == "live":
            # 1.39.13 测试号验收：联网问题分别等了约 30 秒和 21 秒，期间没有任何回复。
            sent_ack = await self._send_guest_reply(
                conversation,
                _LIVE_SEARCH_ACK.get(conversation.language, _LIVE_SEARCH_ACK[Language.ZH]),
                message_type="ack",
            )
        if sent_ack is not None:
            # 最终阶段只携带摘要，避免在任务载荷中复制一份安抚正文。
            fast_ack_sha256 = hashlib.sha256(sent_ack.content.encode("utf-8")).hexdigest()
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
        merged_guest_count = str((message.metadata or {}).get("merged_guest_count", ""))
        if merged_guest_count.isdigit() and int(merged_guest_count) > 1:
            payload["merged_guest_count"] = int(merged_guest_count)
        if fast_ack_sha256 is not None and sent_ack is not None and sent_ack.message_id is not None:
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
            available_at=datetime.now(UTC) + timedelta(seconds=_GUEST_MESSAGE_DEBOUNCE_SECONDS),
            dedupe_key=f"debounce:{message.msgid}",
        )
        if self._commit_boundary is not None:
            await self._commit_boundary()

    async def _send_unrelated_reply(self, conversation: Conversation) -> None:
        """发送固定的非民宿问题边界说明。"""
        await self._send_guest_reply(
            conversation,
            ("I can help with your stay and travel in Wuhan, but cannot answer this topic."
             if conversation.language is Language.EN
             else "我主要协助民宿入住或武汉旅行相关问题，这类问题暂时无法回答。"),
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
        """执行耗时模型和业务副作用，并输出一行主链耗时汇总（1.40.2）。

        外层只计时和记日志：主体逻辑原样不动，异常原样抛出。汇总行记结果类别、
        会话编号、msgid 与各段毫秒数，用来决定并行检索、合并等待是否值得优化。
        """
        timing = _ReplyTiming()
        started = monotonic()
        try:
            await self._process_model_reply_body(
                conversation,
                message,
                discard_if_stale=discard_if_stale,
                timing=timing,
            )
        except BaseException as error:
            timing.outcome = f"error:{type(error).__name__}"
            raise
        finally:
            total_ms = max(0, round((monotonic() - started) * 1000))
            sent_at = message.sent_at if message.sent_at.tzinfo else message.sent_at.replace(
                tzinfo=UTC
            )
            since_sent_ms = max(0, round((datetime.now(UTC) - sent_at).total_seconds() * 1000))
            logger.info(
                "主链耗时：outcome=%s conversation_id=%s msgid=%s total_ms=%s since_sent_ms=%s "
                "context_ms=%s respond_ms=%s post_ms=%s merged=%s stages=%s",
                timing.outcome,
                conversation.id,
                message.msgid,
                total_ms,
                since_sent_ms,
                timing.context_ms,
                timing.respond_ms,
                max(0, total_ms - timing.context_ms - timing.respond_ms),
                (message.metadata or {}).get("merged_guest_count", "1"),
                ",".join(f"{name}:{ms}" for name, ms in timing.stages),
            )

    async def _process_model_reply_body(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        *,
        discard_if_stale: bool,
        timing: "_ReplyTiming",
    ) -> None:
        """执行耗时模型和业务副作用；快速安抚已在前一事务发送。

        timing 只收集耗时与结果类别，不参与任何分支判断。
        """

        stay_repository = (
            self._conversations if isinstance(self._conversations, StayConfirmationPort) else None
        )
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        pending = dict(conversation.stay_confirmation or {})
        confirmed = None
        progress_reply = ""
        try:
            context_started = monotonic()
            model_context = None
            if self._customer_context is not None and conversation.customer_id is not None:
                model_context = await self._customer_context.load_model_context(
                    conversation.customer_id,
                    query=message.content,
                )
            if (
                conversation.customer_id is not None
                and isinstance(self._customer_context, TaskStatusPort)
                and re.search(
                    r"进度|处理好了吗|完成了吗|送到了吗|申请.{0,8}(?:怎么样|有消息)"
                    r"|request status|any update",
                    message.content,
                    re.I,
                )
            ):
                rows = await self._customer_context.list_recent_task_statuses(
                    conversation.customer_id
                )
                # 多个请求不猜指代，列出可核验编号和状态，不让模型宣称完成。
                statuses = {
                    "pending_confirmation": "待管家确认",
                    "pending_assignment": "待分派",
                    "in_progress": "处理中",
                    "pending_inspection": "待检查",
                    "completed": "已完成",
                    "cancelled": "已取消",
                    "expired": "已失效",
                }
                if conversation.language is Language.EN:
                    statuses = dict(zip(statuses, [
                        "awaiting host confirmation", "awaiting assignment", "in progress",
                        "awaiting inspection", "completed", "cancelled", "expired",
                    ], strict=True))
                progress_reply = (
                    "\n".join(
                        (f"Request {row['task_id']}: {statuses.get(str(row['status']), 'unknown')} "
                         f"(updated: {row['updated_at']})."
                         if conversation.language is Language.EN else
                         f"请求 {row['task_id']}：{statuses.get(str(row['status']), '状态待核实')}"
                         f"（记录更新时间：{row['updated_at']}）。")
                        for row in rows
                    )
                    if rows
                    else ("No request records found." if conversation.language is Language.EN
                          else "暂未找到您的请求记录。")
                )
                model_context = replace(model_context or CustomerModelContext(), open_tasks=rows)
            if stay_repository is not None:
                confirmed = await stay_repository.get_confirmed_stay(
                    conversation.id,
                    today=today,
                    lock=False,
                )
                if confirmed is None and pending.get("status") == "confirmed":
                    pending = {}
                model_context = replace(
                    model_context or CustomerModelContext(),
                    stay_confirmation=pending or None,
                    confirmed_stay=confirmed,
                )
            merged_guest_count_text = str((message.metadata or {}).get("merged_guest_count", "1"))
            merged_guest_count = (
                int(merged_guest_count_text) if merged_guest_count_text.isdigit() else 1
            )
            context_messages = await self._messages.build_context(
                conversation.id,
                limit=3,
                through_external_message_id=message.msgid,
                merged_guest_content=(
                    message.content if merged_guest_count > 1 else None
                ),
                merged_guest_count=merged_guest_count,
            )
            timing.context_ms = max(0, round((monotonic() - context_started) * 1000))
            respond_started = monotonic()
            try:
                decision = await self._assistant.respond(
                    guest_identifier=message.external_userid,
                    language=conversation.language,
                    messages=context_messages,
                    customer_context=model_context,
                    stage_timing_sink=timing.add_stage,
                )
            finally:
                timing.respond_ms = max(0, round((monotonic() - respond_started) * 1000))
        except TourismSearchError as error:
            if discard_if_stale and await self._discard_stale_final(
                conversation,
                message,
            ):
                timing.outcome = "stale_discarded"
                return
            timing.outcome = "tourism_failure"
            await self._escalate_tourism_failure(conversation, message, error)
            return
        except AssistantUnavailableError:
            if discard_if_stale and await self._discard_stale_final(
                conversation,
                message,
            ):
                timing.outcome = "stale_discarded"
                return
            if self._determine_handoff_reason(message.content) is None and self._is_facility_issue(
                message.content, None
            ):
                timing.outcome = "facility"
                await self._handle_facility_issue(
                    conversation,
                    message,
                    None,
                )
                return
            timing.outcome = "assistant_unavailable"
            await self._escalate_assistant_failure(conversation, message)
            return
        if (
            decision.task_suggestion
            or decision.handoff_reason
            or decision.facility_issue
            or decision.staff_confirmation_required
            or is_service_request(message.content)
            or is_booking_action_request(message.content)
        ):
            await self._load_notification_names(conversation)
        if discard_if_stale and await self._discard_stale_final(
            conversation,
            message,
        ):
            timing.outcome = "stale_discarded"
            return
        if decision.handoff_reason in {
            "emergency:fire",
            "emergency:gas",
            "emergency:electric",
            "emergency:medical",
            "emergency:violence",
        }:
            await self._escalate_emergency(
                conversation,
                message,
                EmergencyClassification(True, decision.handoff_reason.split(":", 1)[1]),
            )
            return
        confirmation_reply = ""
        if stay_repository is not None:
            # 不把过时肯定回复套用到新提示；订单变化时不用旧事实发送个性化答复。
            current = await stay_repository.get_confirmed_stay(conversation.id, today=today)
            if confirmed is not None and current != confirmed:
                confirmation_reply = await stay_repository.prepare_stay_confirmation(
                    conversation.id,
                    source_message_id=message.msgid,
                    today=today,
                )
                await self._send_guest_reply(conversation, confirmation_reply)
                return
            intent = decision.stay_confirmation_intent
            prompt_id = str(pending.get("prompt_message_id", ""))
            if intent == "confirm" and pending.get("status") == "pending":
                accepted = await stay_repository.confirm_stay(
                    conversation.id,
                    prompt_message_id=prompt_id,
                    guest_message_id=message.msgid,
                    now=datetime.now(UTC),
                )
                confirmation_reply = (
                    ("Your stay dates and room are confirmed." if accepted
                      else "Your stay details have changed. Please confirm again.")
                     if conversation.language is Language.EN else
                     ("本次入住日期和房间已确认。" if accepted
                      else "入住资料发生变化，请重新确认。")
                )
                if not accepted:
                    confirmation_reply += await stay_repository.prepare_stay_confirmation(
                        conversation.id,
                        source_message_id=message.msgid,
                        today=today,
                    )
            elif intent == "decline" and prompt_id:
                await stay_repository.decline_stay(conversation.id, prompt_message_id=prompt_id)
                confirmation_reply = (
                    "Please tell us which dates or room need checking with the host."
                    if conversation.language is Language.EN else
                    "请说明需要核对的日期或房间，管家核实后再确认。"
                )
            elif intent == "select" and decision.stay_order_id is not None:
                confirmation_reply = await stay_repository.prepare_stay_confirmation(
                    conversation.id,
                    source_message_id=message.msgid,
                    today=today,
                    order_id=decision.stay_order_id,
                )
            elif (
                current is None
                and not pending
                and (
                    (model_context is not None and model_context.active_orders)
                    or re.search(
                        r"我的房间|我住的|办理入住|入住确认|my room|my stay", message.content, re.I
                    )
                )
            ):
                confirmation_reply = await stay_repository.prepare_stay_confirmation(
                    conversation.id,
                    source_message_id=message.msgid,
                    today=today,
                )
        local_handoff_reason = self._determine_handoff_reason(message.content)
        if (
            local_handoff_reason is None
            and decision.handoff_reason is None
            and self._is_facility_issue(message.content, decision)
        ):
            timing.outcome = "facility"
            await self._handle_facility_issue(
                conversation,
                message,
                decision.facility_advice,
                extra_reply="\n\n".join(
                    filter(
                        None,
                        (
                            compose_reply_parts(decision.reply_parts),
                            confirmation_reply,
                            progress_reply,
                        ),
                    )
                ),
            )
            return
        # 先登记请求和通知，再根据实际结果组织收尾，模型不能生成成功承诺。
        action_reply = await self._record_task_suggestion(conversation, message, decision)
        high_risk = bool(local_handoff_reason or decision.handoff_reason)
        if high_risk:
            reason = local_handoff_reason or decision.handoff_reason
            await self._activate_human(
                conversation,
                message,
                f"YuMi 接管：{reason}",
                audit_reason=reason,
            )
        prepared_reply = prepare_planned_reply(
            decision.reply_parts, fallback=decision.reply_text,
            language=conversation.language, question=message.content, high_risk=high_risk,
        )
        if progress_reply:
            prepared_reply = f"{prepared_reply}\n\n{progress_reply}"
        if confirmation_reply:
            prepared_reply = f"{prepared_reply}\n\n{confirmation_reply}"
        if action_reply:
            prepared_reply = f"{prepared_reply}\n\n{action_reply}"
        fast_ack_sha256 = str((message.metadata or {}).get("fast_ack_sha256", ""))
        prepared_sha256 = hashlib.sha256(prepared_reply.encode("utf-8")).hexdigest()
        unchanged_ack = (
            not action_reply
            and not confirmation_reply
            and not progress_reply
            and hashlib.sha256(decision.reply_text.encode("utf-8")).hexdigest() == fast_ack_sha256
        )
        if fast_ack_sha256 != prepared_sha256 and not unchanged_ack:
            await self._send_prepared_guest_reply(
                conversation, prepared_reply, stale_exempt=high_risk
            )
        await self._track_frequent_faq(message, decision)

    @staticmethod
    def _is_facility_issue(
        question: str,
        decision: AssistantDecision | None,
    ) -> bool:
        """判断本轮是否走民宿设施或住宿环境问题流程。"""
        if facility_fault_exclusion(question) is not None:
            return False
        if decision is None:
            return has_facility_fault_signal(question)
        issue = decision.facility_issue
        if issue is not None:
            # 结构化归属负责开放语义；私人物品与外部场所不建民宿维修任务。
            return issue.scope not in {"private", "external"}
        # 模型字段缺失时，明确的本地故障信号仍进入流程并使用确定性兜底。
        return has_facility_fault_signal(question)

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
    ) -> str:
        """先幂等登记请求，隔离失败事务；仅从实际结果生成客人收尾。"""
        decision.action_result = GuestActionResult()
        if decision.facility_issue and decision.facility_issue.scope in {"private", "external"}:
            return ""
        booking = is_booking_action_request(message.content)
        requested = (
            any(is_service_request(text) for text in self._policy_questions(message.content))
            or booking
            or self._is_facility_issue(message.content, decision)
        )
        if not requested:
            return ""
        suggestion = decision.task_suggestion
        if suggestion is None:
            suggestion = TaskSuggestion(
                task_type=BusinessTaskType.SPECIAL_SERVICE,
                description=message.content[:500],
            )
        # ponytail: 一条来源消息只有一个任务键；多事项合并，需独立分派时再拆任务模型。
        clauses = re.split(r"[，,；;。]|并且|并|另外|以及|同时| and ", message.content)
        multiple = sum(
            is_service_request(clause) or has_facility_fault_signal(clause)
            for clause in clauses
        ) > 1
        description = TaskSuggestion.redact_sensitive_description(message.content)[:500]
        english = conversation.language is Language.EN
        failure = (
            "Your request could not be registered. Please contact the host directly."
            if english
            else "您的请求暂时未能登记，请直接联系管家确认。"
        )
        if self._business_tasks is None or conversation.customer_id is None:
            return failure
        # 房间和日期不能由模型猜测；未确认的请求保持待确认状态。
        confirmed = {}
        if isinstance(self._conversations, StayConfirmationPort):
            confirmed = (
                await self._conversations.get_confirmed_stay(
                    conversation.id,
                    today=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
                )
                or {}
            )
        property_id = (
            confirmed.get("property_id") if confirmed.get("status") == "confirmed" else None
        )
        try:
            async with self._savepoint_factory():
                task = await self._business_tasks.record_ai_suggestion(
                    customer_id=conversation.customer_id,
                    source_message_id=message.msgid,
                    task_type=BusinessTaskType.SPECIAL_SERVICE
                    if booking or multiple
                    else suggestion.task_type,
                    description=description,
                    property_id=property_id,
                    service_date=None,
                )
        except Exception as error:
            logger.warning("请求登记失败：error_type=%s", type(error).__name__)
            return failure
        self._notification_task_id = task.id
        decision.action_result = GuestActionResult(task_id=task.id, registered=True)
        try:
            async with self._savepoint_factory():
                await self._notify_employee(
                    conversation,
                    message,
                    f"新任务待确认：ID {task.id}，类型 {task.task_type.value}",
                )
        except Exception as error:
            logger.warning("请求通知入队失败：error_type=%s", type(error).__name__)
            return (
                "Your request is registered, but the staff notification failed. "
                "Please contact the host."
                if english
                else "您的请求已登记，但管家通知未成功，请直接联系管家。"
            )
        decision.action_result.notification_queued = True
        return (
            "Your request is registered and a staff notification is queued; it awaits confirmation."
            if english
            else "您的请求已登记，管家通知已提交，尚待管家确认。"
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
        """无持久化测试替身仅判断有效文本；生产由消息历史控制切换。"""
        return substantive_language(text) or fallback

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
            # 以高风险口径发出的正是紧急安全提示、客诉首响和转人工确认：客人连发
            # 「燃气味好重」「我们现在该怎么办」时，第一条的撤离提示曾因排队期间来了
            # 第二条而被整条跳过。普通问答仍按过时处理，新消息会重新生成答案。
            stale_exempt=high_risk,
        )

    async def _send_prepared_guest_reply(
        self,
        conversation: Conversation,
        content: str,
        *,
        message_type: str = "text",
        stale_exempt: bool = False,
    ) -> GuestReplyReceipt:
        """发送已经过统一客人侧策略处理的文本，并记录真实消息编号。

        超过企业微信单条上限的回复拆成多条：生产 outbox 链式入队，保证逐段有序；
        直接发送的发送器按顺序逐条发出。
        """
        self._last_guest_reply = content
        parts = split_guest_reply(content, conversation.language)
        if len(parts) > 1:
            return await self._send_guest_reply_parts(
                conversation,
                parts,
                message_type=message_type,
                stale_exempt=stale_exempt,
            )
        message_id = await self._wecom.send_text(
            conversation.open_kfid,
            conversation.external_userid,
            content,
            message_type=message_type,
            stale_exempt=stale_exempt,
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

    async def _send_guest_reply_parts(
        self,
        conversation: Conversation,
        parts: list[str],
        *,
        message_type: str,
        stale_exempt: bool = False,
    ) -> GuestReplyReceipt:
        """按顺序发出多段回复，回执记录第一段的编号与完整正文。"""
        full_content = "\n\n".join(parts)
        if isinstance(self._wecom, ChainedGuestSenderPort):
            first_id = await self._wecom.send_text_chain(
                conversation.open_kfid,
                conversation.external_userid,
                parts,
                message_type=message_type,
                stale_exempt=stale_exempt,
            )
            return GuestReplyReceipt(content=full_content, message_id=first_id)
        first_id = None
        for part in parts:
            message_id = await self._wecom.send_text(
                conversation.open_kfid,
                conversation.external_userid,
                part,
                message_type=message_type,
                stale_exempt=stale_exempt,
            )
            if first_id is None:
                first_id = message_id
            if message_id is None or message_id.startswith("outbox:"):
                continue
            await self._messages.record_bot(
                conversation.id,
                message_id,
                part,
                message_type=message_type,
            )
        return GuestReplyReceipt(content=full_content, message_id=first_id)

    async def _answer_possible_danger(
        self, conversation: Conversation, message: IncomingMessage,
    ) -> None:
        """不明确的安全情况先提醒和通知；不发撤离模板，不自动接管。"""
        reply = (
            "Please avoid the suspected hazard for now. What is happening at the moment?"
            if conversation.language is Language.EN
            else "请先避开可能有危险的位置。现在具体是什么情况？"
        )
        await self._send_prepared_guest_reply(conversation, reply, stale_exempt=True)
        await self._notify_employee(conversation, message, "可能的安全情况")

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

    async def _escalate_regular(self, conversation: Conversation, message: IncomingMessage) -> None:
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
        """明确告知公开查询失败，不创建人工任务或后续补发承诺。"""
        reply = (
            "Sorry, I couldn’t finish the live search just now."
            if conversation.language is Language.EN
            else "抱歉，实时信息刚才没能查完整。"
        )
        await self._send_guest_reply(
            conversation,
            reply,
            requires_human=False,
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

    async def _load_notification_names(self, conversation: Conversation) -> None:
        """在任务及订单写锁之前读取外部展示名称，本轮通知直接复用。"""
        if self._notification_names is not None:
            return
        customer_service_name, guest_name = "微信客服", "客人"
        if self._identity_resolver is not None:
            try:
                customer_service_name = (
                    await self._identity_resolver.get_kf_account_name(conversation.open_kfid)
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
                logger.warning("读取通知名称失败：error_type=%s", type(error).__name__)
        self._notification_names = (customer_service_name, guest_name)

    async def _notify_employee(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        reason: str,
    ) -> None:
        """向值班员工发送不包含接口密钥的会话摘要。"""
        if self._notification_names is None:
            await self._load_notification_names(conversation)
        customer_service_name, guest_name = self._notification_names or ("微信客服", "客人")
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
        if self._customer_notification is not None and conversation.customer_id is not None:
            try:
                customer_note = await self._customer_notification.get_customer_notification_note(
                    conversation.customer_id
                )
            except Exception as error:
                # CRM 备注查询失败不应阻塞人工通知，继续使用客人名称兜底。
                logger.warning(
                    "客户通知备注读取失败，使用客人名称兜底：error_type=%s",
                    type(error).__name__,
                )
        customer_note = self._employee_notification_label(customer_note)
        display_identity = f"客人备注：{customer_note}" if customer_note else f"客人：{guest_name}"
        reason_label = (
            self._employee_notification_label(
                reason,
                fallback="新任务待确认",
                max_bytes=300,
            )
            or "新任务待确认"
        )
        # 原因与链接优先，原话和实际已登记回复分别限额，不能把排队说成已读。
        labels = {
            **_COMPLAINT_REASON_LABELS, **_EMERGENCY_CATEGORY_LABELS,
            "manual_request_or_media": "客人请求人工协助",
            "assistant_unavailable": "问答服务暂时不可用",
            "early_check_in": "提前入住申请", "price": "价格协商",
            "servicer_reply": "员工正在接待",
        }
        for code, label in labels.items():
            reason_label = re.sub(rf"(?<![a-z_]){re.escape(code)}(?![a-z_])", label, reason_label)
        location = "房间与入住日期：尚未确认"
        if isinstance(self._conversations, StayConfirmationPort):
            stay = await self._conversations.get_confirmed_stay(
                conversation.id, today=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
                lock=False,
            )
            if stay:
                room_name = stay.get("room_number") or stay.get("property_title") or "待核实"
                location = (f"房间：{room_name}；"
                            f"入住：{stay.get('check_in_date')}，退房：{stay.get('check_out_date')}")
        base = self._approval_base_url.rstrip("/")
        link = (f"{base}/employee/tasks/{self._notification_task_id}"
                if self._notification_task_id else
                f"{base}/employee/customers/{conversation.customer_id}")
        await self._wecom.send_internal_text(
            agent_id=self._agent_id, employee_userids=self._duty_employee_userids,
            content=format_employee_notification(
                reason=reason_label, account=customer_service_name, guest=display_identity,
                room=location, link=link, original=" ".join(message.content.split()),
                replied=self._last_guest_reply,
            ),
        )
