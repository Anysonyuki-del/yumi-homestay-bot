import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import (
    BusinessTaskType,
    ConversationMode,
    JobStatus,
    Language,
    MessageOrigin,
)
from homestay_bot.domain.models import Conversation, Customer
from homestay_bot.integrations.deepseek_client import (
    AssistantDecision,
    AssistantUnavailableError,
    BookingFields,
    TaskSuggestion,
)
from homestay_bot.integrations.tourism import (
    TourismSearchError,
    WebSearchStatus,
)
from homestay_bot.services.complaint_service import ComplaintService
from homestay_bot.services.context_retention import CustomerModelContext
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.message_service import IncomingMessage


class ConversationRepositoryStub:
    """在内存中维护单个会话。"""

    def __init__(self) -> None:
        self.locked_ids: list[int] = []
        self.conversation = Conversation(
            id=1,
            open_kfid="wk-1",
            external_userid="wm-1",
            language=Language.ZH,
            mode=ConversationMode.BOT_ACTIVE,
        )

    async def get_or_create(self, message: IncomingMessage) -> Conversation:
        """返回固定会话。"""
        return self.conversation

    async def save(self, conversation: Conversation) -> None:
        """保留更新后的会话。"""
        self.conversation = conversation

    async def lock_activity(self, conversation_id: int) -> None:
        """记录会话活动锁，并验证锁定当前会话。"""
        assert conversation_id == self.conversation.id
        self.locked_ids.append(conversation_id)


class MessageServiceStub:
    """记录消息并支持模拟重复消息。"""

    def __init__(
        self,
        *,
        is_new: bool = True,
        activity_after_boundary: bool | None = None,
    ) -> None:
        self.is_new = is_new
        self.recorded: list[IncomingMessage] = []
        self.bot_messages: list[tuple[int, str, str]] = []
        self.activity_after_boundary = activity_after_boundary

    async def record_incoming(
        self, conversation_id: int, message: IncomingMessage
    ) -> bool:
        """记录入站消息并返回是否首次出现。"""
        self.recorded.append(message)
        return self.is_new

    async def record_bot(
        self,
        conversation_id: int,
        message_id: str,
        content: str,
        sent_at=None,
        message_type: str = "text",
    ) -> None:
        """记录机器人出站消息。"""
        self.bot_messages.append((conversation_id, message_id, content))

    async def build_context(
        self,
        conversation_id: int,
        limit: int = 20,
        through_external_message_id: str | None = None,
        *,
        merged_guest_content: str | None = None,
        merged_guest_count: int = 1,
    ) -> list[dict[str, str]]:
        """把测试中已记录的客人文本转换为模型历史。"""
        recorded = self.recorded
        if through_external_message_id is not None:
            boundary = next(
                (
                    index + 1
                    for index, item in enumerate(recorded)
                    if item.msgid == through_external_message_id
                ),
                len(recorded),
            )
            recorded = recorded[:boundary]
        context = [
            {"role": "user", "content": item.content}
            for item in recorded[-limit:]
            if item.origin is MessageOrigin.GUEST and item.msgtype == "text"
        ]
        if merged_guest_content is not None and merged_guest_count > 1:
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
    ):
        """把测试记录中截至来源边界的连续客人文本合并为一批。"""
        boundary = next(
            index + 1
            for index, item in enumerate(self.recorded)
            if item.msgid == through_external_message_id
        )
        selected = [
            item
            for item in self.recorded[:boundary]
            if item.origin is MessageOrigin.GUEST and item.msgtype == "text"
        ][-max_messages:]
        content = "\n".join(item.content for item in selected)[-max_characters:]
        return SimpleNamespace(content=content, message_count=len(selected))

    async def has_newer_conversation_activity(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源消息之后是否已有客人或员工活动。"""
        if self.activity_after_boundary is not None:
            return self.activity_after_boundary
        for index, item in enumerate(self.recorded):
            if item.msgid == external_message_id:
                return any(
                    newer.origin is not MessageOrigin.BOT
                    for newer in self.recorded[index + 1 :]
                )
        return False

    async def has_newer_guest_message(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """兼容最终阶段已有接口，只检查后续客人文本。"""
        guest_messages = [
            item for item in self.recorded if item.origin is MessageOrigin.GUEST
        ]
        for index, item in enumerate(guest_messages):
            if item.msgid == external_message_id:
                return index < len(guest_messages) - 1
        return False


class CustomerProfileStub:
    """记录会话服务是否在处理消息前确保正式客户存在。"""

    def __init__(self) -> None:
        """初始化固定客户和调用记录。"""
        self.customer = Customer(id=42, display_name="微信客户")
        self.messages: list[IncomingMessage] = []

    async def ensure_for_message(self, message: IncomingMessage) -> Customer:
        """返回固定客户并记录消息。"""
        self.messages.append(message)
        return self.customer


class CustomerContextStub:
    """返回固定脱敏客户摘要。"""

    async def load_model_context(
        self, customer_id: int, *, query: str = ""
    ) -> CustomerModelContext:
        """验证按正式客户主键和当前问题读取摘要。"""
        assert customer_id == 42
        assert query == "几点入住？"
        return CustomerModelContext("偏好安静", "曾入住", [])


class AssistantStub:
    """返回固定客服决定并统计调用次数。"""

    def __init__(
        self,
        *,
        handoff_reason: str | None = None,
        decision: AssistantDecision | None = None,
    ) -> None:
        self.calls = 0
        self.ack_calls = 0
        self.handoff_reason = handoff_reason
        self.decision = decision
        self.last_kwargs = None
        self.last_ack_kwargs = None

    async def respond(self, **kwargs) -> AssistantDecision:
        """生成固定中文回复。"""
        self.calls += 1
        self.last_kwargs = kwargs
        if self.decision is not None:
            return self.decision
        return AssistantDecision(
            reply_text="下午三点后可以入住。",
            language=Language.ZH,
            intent="faq",
            confidence=0.98,
            handoff_reason=self.handoff_reason,
        )

    async def respond_ack(self, **kwargs) -> str:
        """返回固定温暖安抚。"""
        self.ack_calls += 1
        self.last_ack_kwargs = kwargs
        return "收到啦，我来帮您看看。"


class FailingTourismAssistantStub(AssistantStub):
    """模拟 DeepSeek 联网超时或不支持。"""

    def __init__(self, status: WebSearchStatus) -> None:
        """保存预期失败分类。"""
        super().__init__()
        self.status = status

    async def respond(self, **kwargs) -> AssistantDecision:
        """抛出可识别的旅游联网异常。"""
        self.calls += 1
        raise TourismSearchError(self.status)


class FailingAssistantStub(AssistantStub):
    """模拟 DeepSeek 普通客服无法生成安全回复。"""

    async def respond(self, **kwargs) -> AssistantDecision:
        """抛出统一模型不可用异常。"""
        self.calls += 1
        raise AssistantUnavailableError()


class BlockingAssistantStub(AssistantStub):
    """让正式模型停在门闩处，复现模型运行中出现员工回复。"""

    def __init__(self) -> None:
        """初始化开始和释放事件。"""
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def respond(self, **kwargs) -> AssistantDecision:
        """标记模型已开始，等待测试并发写入新活动后返回。"""
        self.calls += 1
        self.last_kwargs = kwargs
        self.started.set()
        await self.release.wait()
        return AssistantDecision(
            reply_text="我们会处理。",
            language=Language.ZH,
            intent="maintenance",
            confidence=0.95,
            task_suggestion=TaskSuggestion(
                task_type=BusinessTaskType.MAINTENANCE,
                description="检查灯具",
            ),
        )


class WeComStub:
    """记录发送给客人及值班员工的消息。"""

    def __init__(self) -> None:
        self.guest_messages: list[str] = []
        self.internal_messages: list[str] = []

    async def send_text(
        self,
        open_kfid: str,
        external_userid: str,
        content: str,
        *,
        message_type: str = "text",
    ) -> str:
        """记录客人消息并返回企业微信消息编号。"""
        self.guest_messages.append(content)
        return f"bot-{len(self.guest_messages)}"

    async def send_internal_text(
        self, *, agent_id: int, employee_userids: list[str], content: str
    ) -> None:
        """记录内部升级通知。"""
        self.internal_messages.append(content)


class OutboxWeComStub(WeComStub):
    """模拟生产事务 outbox，只登记消息而不代表企业微信已接受。"""

    async def send_text(
        self,
        open_kfid: str,
        external_userid: str,
        content: str,
        *,
        message_type: str = "text",
    ) -> str:
        """记录待发送正文并返回稳定 outbox 编号。"""
        self.guest_messages.append(content)
        return "outbox:fast-ack"


class IdentityResolverStub:
    """返回员工通知中使用的客服账号和客人显示名。"""

    async def get_kf_account_name(self, open_kfid: str) -> str | None:
        """返回固定客服账号名称。"""
        return "YuMi客服"

    async def get_kf_customer_name(
        self, open_kfid: str, external_userid: str
    ) -> str | None:
        """返回固定客人名称用于房间号缺失时的兜底。"""
        return "张三"


class UnsafeIdentityResolverStub:
    """返回带换行和伪字段的外部展示名，验证通知出口统一清理。"""

    async def get_kf_account_name(self, open_kfid: str) -> str | None:
        """返回带换行的客服账号名。"""
        return "YuMi客服\n消息：伪造字段"

    async def get_kf_customer_name(
        self, open_kfid: str, external_userid: str
    ) -> str | None:
        """返回带换行的客人昵称。"""
        return "张三\n客服账号：伪造字段"



class CustomerNotificationStub:
    """返回员工通知优先使用的 CRM 自动入住备注。"""

    async def get_customer_notification_note(self, customer_id: int) -> str | None:
        """返回固定自动入住备注。"""
        return "8.14-8.16《春和景明》"


class CustomerNotificationStubWithoutNote:
    """模拟客户没有自动或员工备注的情况。"""

    async def get_customer_notification_note(self, customer_id: int) -> str | None:
        """返回空值，验证员工通知的客人名称兜底。"""
        return None


class CustomerNotificationFailureStub:
    """模拟 CRM 备注读取异常。"""

    async def get_customer_notification_note(self, customer_id: int) -> str | None:
        """抛出不应进入员工通知的敏感异常。"""
        raise RuntimeError("SECRET_DATABASE_DETAIL")


class UnsafeCustomerNotificationStub:
    """返回带换行的备注，验证服务出口再次清理。"""

    async def get_customer_notification_note(self, customer_id: int) -> str | None:
        """返回可伪造通知字段的测试备注。"""
        return "8.14-8.16《春和景明》\n消息：伪造字段"


class LongChineseCustomerNotificationStub:
    """返回长中文备注，验证企业微信通知的 UTF-8 总字节预算。"""

    async def get_customer_notification_note(self, customer_id: int) -> str | None:
        """返回足以触发企业微信正文上限的中文备注。"""
        return "春" * 200


class ComplaintReviewStub:
    """记录客诉来源并返回固定客诉编号。"""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str, str]] = []

    async def create_or_get(
        self,
        *,
        conversation_id: int,
        source_message_id: str,
        reason: str,
        risk_level: str,
    ):
        """记录客诉创建参数。"""
        self.calls.append((conversation_id, source_message_id, reason, risk_level))
        return SimpleNamespace(id=17)


class DeferredJobStub:
    """记录快速安抚阶段登记的最终处理任务。"""

    def __init__(self) -> None:
        self.jobs: list[
            tuple[str, dict[str, object], str | None, datetime | None]
        ] = []
        self.delivery_status: JobStatus | None = None

    async def enqueue(
        self,
        job_type,
        payload,
        *,
        available_at=None,
        dedupe_key=None,
    ):
        """保存任务类型和稳定去重键。"""
        self.jobs.append((job_type, payload, dedupe_key, available_at))
        return SimpleNamespace()

    async def status_for_dedupe_key(self, dedupe_key: str) -> JobStatus | None:
        """返回测试配置的快速安抚投递任务状态。"""
        assert dedupe_key == "outbox:fast-ack"
        return self.delivery_status


class ApprovalServiceStub:
    """记录完整预订资料只创建待审批单。"""

    def __init__(self) -> None:
        self.calls = []

    async def create_pending(
        self, conversation_id, request, *, source_message_id=None
    ):
        """记录会话和预订资料，不调用任何百居易写接口。"""
        self.calls.append((conversation_id, request))
        return SimpleNamespace(id=9, approval_code="APP-9")


class FrequentFaqStub:
    """记录高频 FAQ 跟踪调用并验证客人回复已先生成。"""

    def __init__(self, wecom: WeComStub | None = None, *, fail: bool = False) -> None:
        """配置可选发送端观察器和失败行为。"""
        self.wecom = wecom
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    async def track(self, **kwargs) -> None:
        """保存调用参数，必要时模拟统计异常。"""
        if self.wecom is not None:
            assert self.wecom.guest_messages
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("candidate tracking failed")


class BusinessTaskStub:
    """记录会话服务写入的 AI 待确认任务。"""

    def __init__(self, *, fail: bool = False) -> None:
        """配置可选失败行为。"""
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    async def record_ai_suggestion(self, **kwargs):
        """记录结构化建议并返回固定任务。"""
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("task unavailable")
        return SimpleNamespace(
            id=8,
            task_type=kwargs["task_type"],
        )


class ConversationAuditStub:
    """记录人工接管审计，不保存聊天正文。"""

    def __init__(self) -> None:
        """初始化审计调用。"""
        self.calls: list[dict[str, object]] = []

    async def record_handoff(self, **kwargs) -> None:
        """记录最小接管元数据。"""
        self.calls.append(kwargs)


def incoming(
    *,
    content: str = "几点入住？",
    origin: MessageOrigin = MessageOrigin.GUEST,
    msgtype: str = "text",
    msgid: str = "msg-1",
) -> IncomingMessage:
    """构造一条可复用的标准入站消息。"""
    return IncomingMessage(
        msgid=msgid,
        open_kfid="wk-1",
        external_userid="wm-1",
        origin=origin,
        msgtype=msgtype,
        content=content,
        sent_at=datetime(2026, 7, 29, tzinfo=UTC),
    )


def build_service(
    *,
    assistant: AssistantStub | None = None,
    wecom: WeComStub | None = None,
    messages: MessageServiceStub | None = None,
    approvals: ApprovalServiceStub | None = None,
    frequent_faq: FrequentFaqStub | None = None,
    customer_profiles: CustomerProfileStub | None = None,
    customer_context: CustomerContextStub | None = None,
    business_tasks=None,
    audit_events=None,
    jobs=None,
    identity_resolver=None,
    customer_notification=None,
    complaint_service=None,
    complaint_reviews=None,
    defer_model: bool = False,
    commit_boundary=None,
) -> tuple[ConversationService, ConversationRepositoryStub, AssistantStub, WeComStub]:
    """创建注入固定依赖的会话服务。"""
    conversations = ConversationRepositoryStub()
    selected_assistant = assistant or AssistantStub()
    selected_wecom = wecom or WeComStub()
    service = ConversationService(
        conversations=conversations,
        messages=messages or MessageServiceStub(),
        assistant=selected_assistant,
        emergency_service=EmergencyService(),
        wecom=selected_wecom,
        agent_id=100001,
        duty_employee_userids=["staff-1"],
        approvals=approvals,
        frequent_faq=frequent_faq,
        customer_profiles=customer_profiles,
        customer_context=customer_context,
        business_tasks=business_tasks,
        audit_events=audit_events,
        jobs=jobs,
        defer_model=defer_model,
        commit_boundary=commit_boundary,
        identity_resolver=identity_resolver,
        customer_notification=customer_notification,
        complaint_service=complaint_service,
        complaint_reviews=complaint_reviews,
    )
    return service, conversations, selected_assistant, selected_wecom


@pytest.mark.asyncio
async def test_deferred_normal_message_waits_three_seconds_before_ack() -> None:
    """普通文本先登记三秒静默任务，入站事务内不得提前发送安抚。"""
    jobs = DeferredJobStub()
    commits = 0

    async def commit() -> None:
        """记录静默任务与入站消息一起提交。"""
        nonlocal commits
        commits += 1

    service, _, assistant, wecom = build_service(
        jobs=jobs,
        defer_model=True,
        commit_boundary=commit,
    )
    before = datetime.now(UTC)

    await service.handle_message(incoming(content="请补两瓶矿泉水"))

    after = datetime.now(UTC)
    assert assistant.ack_calls == 0
    assert wecom.guest_messages == []
    assert len(jobs.jobs) == 1
    job_type, payload, dedupe_key, available_at = jobs.jobs[0]
    assert job_type == "wecom_process_message"
    assert payload["phase"] == "debounce"
    assert dedupe_key == "debounce:msg-1"
    assert available_at is not None
    assert before + timedelta(seconds=3) <= available_at <= after + timedelta(seconds=3)
    assert commits == 1


@pytest.mark.asyncio
async def test_debounce_merges_fragments_before_single_ack_and_final_job() -> None:
    """三秒静默后才按完整问题判断安抚，并只登记一次最终任务。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    fragments = [
        incoming(content="你好，房间里的灯", msgid="msg-1"),
        incoming(content="一直闪", msgid="msg-2"),
        incoming(content="麻烦帮我安排维修", msgid="msg-3"),
    ]
    messages.recorded.extend(fragments)
    service, _, assistant, wecom = build_service(
        jobs=jobs,
        messages=messages,
        wecom=OutboxWeComStub(),
    )

    await service.process_debounced_message(fragments[-1])

    merged = "你好，房间里的灯\n一直闪\n麻烦帮我安排维修"
    assert assistant.ack_calls == 1
    assert assistant.last_ack_kwargs["question"] == merged
    assert len(wecom.guest_messages) == 1
    assert len(jobs.jobs) == 1
    _, payload, dedupe_key, available_at = jobs.jobs[0]
    assert payload["phase"] == "final"
    assert payload["content"] == merged
    assert payload["merged_guest_count"] == 3
    assert dedupe_key == "final:msg-3"
    assert available_at is None


@pytest.mark.asyncio
async def test_outdated_debounce_task_exits_before_ack_or_model() -> None:
    """同一会话已有更新片段时，旧静默任务不得产生任何客人输出。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    first = incoming(content="房间里的灯", msgid="msg-1")
    second = incoming(content="一直闪", msgid="msg-2")
    messages.recorded.extend([first, second])
    service, _, assistant, wecom = build_service(
        jobs=jobs,
        messages=messages,
    )

    await service.process_debounced_message(first)

    assert assistant.calls == 0
    assert assistant.ack_calls == 0
    assert wecom.guest_messages == []
    assert jobs.jobs == []


@pytest.mark.asyncio
async def test_debounced_information_fragments_skip_ack_but_enqueue_one_final() -> None:
    """合并后的信息问题不发泛化安抚，只登记一次最终回答。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    fragments = [
        incoming(content="想问一下", msgid="msg-1"),
        incoming(content="明天入住后", msgid="msg-2"),
        incoming(content="几点可以退房？", msgid="msg-3"),
    ]
    messages.recorded.extend(fragments)
    service, _, assistant, wecom = build_service(jobs=jobs, messages=messages)

    await service.process_debounced_message(fragments[-1])

    assert assistant.ack_calls == 0
    assert wecom.guest_messages == []
    assert len(jobs.jobs) == 1
    assert jobs.jobs[0][1]["phase"] == "final"
    assert jobs.jobs[0][1]["merged_guest_count"] == 3


@pytest.mark.asyncio
async def test_merged_batch_reaches_model_as_one_question_once() -> None:
    """最终阶段只调用一次模型，并把本轮片段折叠为一个完整问题。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    fragments = [
        incoming(content="想问一下", msgid="msg-1"),
        incoming(content="明天入住后", msgid="msg-2"),
        incoming(content="几点可以退房？", msgid="msg-3"),
    ]
    messages.recorded.extend(fragments)
    service, _, assistant, _ = build_service(jobs=jobs, messages=messages)

    await service.process_debounced_message(fragments[-1])
    payload = jobs.jobs[-1][1]
    final_message = IncomingMessage(
        msgid="msg-3",
        open_kfid="wk-1",
        external_userid="wm-1",
        origin=MessageOrigin.GUEST,
        msgtype="text",
        content=str(payload["content"]),
        sent_at=fragments[-1].sent_at,
        metadata={"merged_guest_count": str(payload["merged_guest_count"])},
    )

    await service.process_recorded_message(final_message)

    assert assistant.calls == 1
    assert assistant.last_kwargs["messages"] == [
        {
            "role": "user",
            "content": "想问一下\n明天入住后\n几点可以退房？",
        }
    ]


@pytest.mark.asyncio
async def test_merged_fragments_are_rechecked_for_emergency_before_ack() -> None:
    """只有合并后才完整的门锁紧急语义必须进入固定安全流程。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    fragments = [
        incoming(content="门锁现在", msgid="msg-1"),
        incoming(content="完全坏了", msgid="msg-2"),
    ]
    messages.recorded.extend(fragments)
    service, conversations, assistant, wecom = build_service(
        jobs=jobs,
        messages=messages,
    )

    await service.process_debounced_message(fragments[-1])

    assert assistant.calls == 0
    assert assistant.ack_calls == 0
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert "安全" in wecom.guest_messages[0]
    assert jobs.jobs == []


@pytest.mark.asyncio
async def test_merged_fragments_are_rechecked_for_complaint_before_ack() -> None:
    """拆开的激烈客诉语义合并后只允许固定客诉安抚。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    fragments = [
        incoming(content="这也太", msgid="msg-1"),
        incoming(content="离谱了", msgid="msg-2"),
    ]
    messages.recorded.extend(fragments)
    service, conversations, assistant, wecom = build_service(
        jobs=jobs,
        messages=messages,
        complaint_service=ComplaintService(),
    )

    await service.process_debounced_message(fragments[-1])

    assert assistant.calls == 0
    assert assistant.ack_calls == 0
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert wecom.guest_messages == [ComplaintService.guest_acknowledgement()]
    assert jobs.jobs == []


@pytest.mark.asyncio
async def test_split_english_human_request_is_rechecked_after_merge() -> None:
    """英文转人工短语被拆成两条时，合并后仍须立即进入人工流程。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    fragments = [
        incoming(content="Please find a human", msgid="msg-1"),
        incoming(content="agent for me", msgid="msg-2"),
    ]
    messages.recorded.extend(fragments)
    service, conversations, assistant, wecom = build_service(
        jobs=jobs,
        messages=messages,
    )

    await service.process_debounced_message(fragments[-1])

    assert assistant.calls == 0
    assert assistant.ack_calls == 0
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert len(wecom.guest_messages) == 1
    assert jobs.jobs == []


@pytest.mark.asyncio
async def test_split_supply_request_triggers_one_ack_after_merge() -> None:
    """服务动作和物品被拆成两条时，跨行合并仍须发送一次安抚。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    fragments = [
        incoming(content="请帮我补", msgid="msg-1"),
        incoming(content="两瓶矿泉水", msgid="msg-2"),
    ]
    messages.recorded.extend(fragments)
    service, _, assistant, wecom = build_service(jobs=jobs, messages=messages)

    await service.process_debounced_message(fragments[-1])

    assert assistant.ack_calls == 1
    assert len(wecom.guest_messages) == 1
    assert len(jobs.jobs) == 1


@pytest.mark.asyncio
async def test_split_early_check_in_uses_normalized_final_handoff_reason() -> None:
    """跨消息拆词的提前入住必须在最终阶段通知员工并切人工。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    fragments = [
        incoming(content="想申请提", msgid="msg-1"),
        incoming(content="前入住", msgid="msg-2"),
    ]
    messages.recorded.extend(fragments)
    service, conversations, assistant, wecom = build_service(
        jobs=jobs,
        messages=messages,
    )

    await service.process_debounced_message(fragments[-1])
    payload = jobs.jobs[-1][1]
    await service.process_recorded_message(
        IncomingMessage(
            msgid="msg-2",
            open_kfid="wk-1",
            external_userid="wm-1",
            origin=MessageOrigin.GUEST,
            msgtype="text",
            content=str(payload["content"]),
            sent_at=fragments[-1].sent_at,
            metadata={"merged_guest_count": "2"},
        )
    )

    assert assistant.calls == 1
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert len(wecom.internal_messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "newer_origin,newer_type",
    [
        (MessageOrigin.GUEST, "image"),
        (MessageOrigin.SERVICER, "text"),
    ],
)
async def test_non_text_or_servicer_activity_cancels_old_debounce(
    newer_origin: MessageOrigin,
    newer_type: str,
) -> None:
    """图片、语音或员工回复出现后，旧文本静默任务不得继续出站。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    first = incoming(content="请补矿泉水", msgid="msg-1")
    newer = incoming(
        content="media-or-reply",
        msgid="msg-2",
        origin=newer_origin,
        msgtype=newer_type,
    )
    messages.recorded.extend([first, newer])
    service, _, assistant, wecom = build_service(jobs=jobs, messages=messages)

    await service.process_debounced_message(first)

    assert assistant.calls == 0
    assert assistant.ack_calls == 0
    assert wecom.guest_messages == []
    assert jobs.jobs == []


@pytest.mark.asyncio
async def test_incoming_and_debounce_paths_lock_conversation_before_activity_checks() -> None:
    """入站和静默消费必须共用会话行锁，闭合检查到写 outbox 的竞态。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub(activity_after_boundary=True)
    service, conversations, _, _ = build_service(
        jobs=jobs,
        messages=messages,
        defer_model=True,
    )
    message = incoming(content="请补矿泉水")

    await service.handle_message(message)
    await service.process_debounced_message(message)

    assert conversations.locked_ids == [1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "newer_origin,newer_type",
    [
        (MessageOrigin.GUEST, "image"),
        (MessageOrigin.SERVICER, "text"),
    ],
)
async def test_final_exits_before_model_after_non_bot_activity(
    newer_origin: MessageOrigin,
    newer_type: str,
) -> None:
    """ACK 后出现图片或员工回复时，旧 final 不得再调用模型。"""
    messages = MessageServiceStub()
    final_message = incoming(content="请补矿泉水", msgid="msg-1")
    messages.recorded.extend(
        [
            final_message,
            incoming(
                content="new activity",
                msgid="msg-2",
                origin=newer_origin,
                msgtype=newer_type,
            ),
        ]
    )
    service, _, assistant, wecom = build_service(messages=messages)

    await service.process_recorded_message(final_message)

    assert assistant.calls == 0
    assert wecom.guest_messages == []


@pytest.mark.asyncio
async def test_servicer_reply_during_model_cancels_final_and_side_effects() -> None:
    """模型运行中员工已回复时，旧 final 必须在锁内复查后无副作用退出。"""
    messages = MessageServiceStub()
    source = incoming(content="请补矿泉水", msgid="msg-1")
    messages.recorded.append(source)
    assistant = BlockingAssistantStub()
    tasks = BusinessTaskStub()
    service, conversations, _, wecom = build_service(
        assistant=assistant,
        messages=messages,
        business_tasks=tasks,
    )
    conversations.conversation.customer_id = 42

    final_task = asyncio.create_task(service.process_recorded_message(source))
    await assistant.started.wait()
    await service.handle_message(
        incoming(
            content="人工已经回复",
            msgid="msg-2",
            origin=MessageOrigin.SERVICER,
        )
    )
    assistant.release.set()
    await final_task

    assert assistant.calls == 1
    assert wecom.guest_messages == []
    assert tasks.calls == []
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE


@pytest.mark.asyncio
async def test_deferred_message_sends_model_ack_and_enqueues_final_task() -> None:
    """静默结束后应先发送模型安抚，再登记最终处理任务。"""
    jobs = DeferredJobStub()
    commits = 0

    async def commit() -> None:
        nonlocal commits
        commits += 1

    service, _, assistant, wecom = build_service(
        jobs=jobs,
        defer_model=True,
        commit_boundary=commit,
    )

    await service.handle_message(incoming(content="请补两瓶矿泉水吗？"))
    assert assistant.ack_calls == 0

    await service.process_debounced_message(incoming(content="请补两瓶矿泉水吗？"))

    assert assistant.calls == 0
    assert assistant.ack_calls == 1
    assert wecom.guest_messages == [
        "我已收到您的诉求。我会立即联系管家来处理，请您稍等。"
    ]
    assert jobs.jobs[-1][0] == "wecom_process_message"
    assert jobs.jobs[-1][2] == "final:msg-1"
    assert len(str(jobs.jobs[-1][1]["fast_ack_sha256"])) == 64
    assert commits == 2


@pytest.mark.asyncio
async def test_deferred_final_skips_exact_duplicate_of_fast_ack() -> None:
    """最终安全回复与已发安抚完全相同时，只允许客人收到一次。"""
    jobs = DeferredJobStub()
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="我已收到您的诉求。",
            language=Language.ZH,
            intent="maintenance",
            confidence=0.96,
        )
    )
    message = incoming(content="灯坏了修一下")
    service, _, _, wecom = build_service(
        assistant=assistant,
        jobs=jobs,
        defer_model=True,
    )

    await service.handle_message(message)
    await service.process_debounced_message(message)
    payload = jobs.jobs[-1][1]
    deferred_message = IncomingMessage(
        msgid=message.msgid,
        open_kfid=message.open_kfid,
        external_userid=message.external_userid,
        origin=message.origin,
        msgtype=message.msgtype,
        content=message.content,
        sent_at=message.sent_at,
        metadata={"fast_ack_sha256": str(payload["fast_ack_sha256"])},
    )

    await service.process_recorded_message(deferred_message)

    assert wecom.guest_messages == [
        "我已收到您的诉求。我会立即联系管家来处理，请您稍等。"
    ]


@pytest.mark.asyncio
async def test_deferred_final_keeps_new_advice_after_fast_ack() -> None:
    """最终回复含有新的安全排障建议时，仍应在快速安抚后发送。"""
    jobs = DeferredJobStub()
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="很抱歉给您添麻烦了，请先关闭故障灯具的电源。",
            language=Language.ZH,
            intent="maintenance",
            confidence=0.96,
        )
    )
    message = incoming(content="灯坏了修一下")
    service, _, _, wecom = build_service(
        assistant=assistant,
        jobs=jobs,
        defer_model=True,
    )

    await service.handle_message(message)
    await service.process_debounced_message(message)
    payload = jobs.jobs[-1][1]
    deferred_message = IncomingMessage(
        msgid=message.msgid,
        open_kfid=message.open_kfid,
        external_userid=message.external_userid,
        origin=message.origin,
        msgtype=message.msgtype,
        content=message.content,
        sent_at=message.sent_at,
        metadata={"fast_ack_sha256": str(payload["fast_ack_sha256"])},
    )

    await service.process_recorded_message(deferred_message)

    assert len(wecom.guest_messages) == 2
    assert "关闭故障灯具的电源" in wecom.guest_messages[1]


@pytest.mark.asyncio
async def test_deferred_final_waits_until_fast_ack_delivery_finishes() -> None:
    """快速安抚仍在 outbox 发送中时，最终任务不得提前调用模型。"""
    jobs = DeferredJobStub()
    jobs.delivery_status = JobStatus.PENDING
    wecom = OutboxWeComStub()
    assistant = AssistantStub()
    message = incoming(content="灯坏了修一下")
    service, _, _, _ = build_service(
        assistant=assistant,
        jobs=jobs,
        wecom=wecom,
        defer_model=True,
    )

    await service.handle_message(message)
    await service.process_debounced_message(message)
    payload = jobs.jobs[-1][1]
    deferred_message = IncomingMessage(
        msgid=message.msgid,
        open_kfid=message.open_kfid,
        external_userid=message.external_userid,
        origin=message.origin,
        msgtype=message.msgtype,
        content=message.content,
        sent_at=message.sent_at,
        metadata={
            "fast_ack_sha256": str(payload["fast_ack_sha256"]),
            "fast_ack_outbox_id": str(payload["fast_ack_outbox_id"]),
        },
    )

    with pytest.raises(RuntimeError, match="快速安抚仍在发送中"):
        await service.process_recorded_message(deferred_message)

    assert assistant.calls == 0


@pytest.mark.asyncio
async def test_deferred_final_is_not_suppressed_when_fast_ack_delivery_failed() -> None:
    """快速安抚投递失败时必须发送最终回复，不能让客人收不到任何内容。"""
    jobs = DeferredJobStub()
    jobs.delivery_status = JobStatus.FAILED
    wecom = OutboxWeComStub()
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="我已收到您的诉求。",
            language=Language.ZH,
            intent="maintenance",
            confidence=0.96,
        )
    )
    message = incoming(content="灯坏了修一下")
    service, _, _, _ = build_service(
        assistant=assistant,
        jobs=jobs,
        wecom=wecom,
        defer_model=True,
    )

    await service.handle_message(message)
    await service.process_debounced_message(message)
    payload = jobs.jobs[-1][1]
    deferred_message = IncomingMessage(
        msgid=message.msgid,
        open_kfid=message.open_kfid,
        external_userid=message.external_userid,
        origin=message.origin,
        msgtype=message.msgtype,
        content=message.content,
        sent_at=message.sent_at,
        metadata={
            "fast_ack_sha256": str(payload["fast_ack_sha256"]),
            "fast_ack_outbox_id": str(payload["fast_ack_outbox_id"]),
        },
    )

    await service.process_recorded_message(deferred_message)

    assert len(wecom.guest_messages) == 2


@pytest.mark.parametrize(
    "question",
    [
        "武汉三日游怎么安排？",
        "房间加湿器怎么用？",
        "有什么伴手礼可以送朋友？",
    ],
)
def test_information_questions_do_not_trigger_fast_service_ack(question: str) -> None:
    """旅游、设施用法和送礼咨询不能被裸动作词误判为人工服务。"""
    assert ConversationService._should_send_fast_ack(question) is False


@pytest.mark.parametrize(
    "question",
    ["请补两瓶矿泉水", "洗衣机一直显示锁，打不开", "想申请提前入住"],
)
def test_operational_requests_still_trigger_fast_service_ack(question: str) -> None:
    """收窄识别后，补给、维修和提前入住仍须快速安抚。"""
    assert ConversationService._should_send_fast_ack(question) is True


@pytest.mark.asyncio
async def test_deferred_room_information_skips_unnecessary_ack() -> None:
    """房间介绍等信息问题应直接排最终回复，不发送泛化安抚。"""
    jobs = DeferredJobStub()
    commits = 0

    async def commit() -> None:
        nonlocal commits
        commits += 1

    service, _, assistant, wecom = build_service(
        jobs=jobs,
        defer_model=True,
        commit_boundary=commit,
    )

    await service.handle_message(incoming(content="介绍一下这间房"))
    await service.process_debounced_message(incoming(content="介绍一下这间房"))

    assert assistant.calls == 0
    assert assistant.ack_calls == 0
    assert wecom.guest_messages == []
    assert jobs.jobs[-1][0] == "wecom_process_message"
    assert jobs.jobs[-1][1]["phase"] == "final"
    assert commits == 2


@pytest.mark.asyncio
async def test_complaint_enters_human_mode_and_skips_final_model_reply() -> None:
    """客诉触发后只发固定安抚，不再调用普通客服模型。"""
    jobs = DeferredJobStub()
    reviews = ComplaintReviewStub()
    service, conversations, assistant, wecom = build_service(
        jobs=jobs,
        complaint_service=ComplaintService(),
        complaint_reviews=reviews,
    )

    await service.handle_message(incoming(content="我要退款，不处理我就投诉平台"))

    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert assistant.calls == 0
    assert wecom.guest_messages == [ComplaintService.guest_acknowledgement()]
    assert reviews.calls == [(1, "msg-1", "refund", "critical")]
    assert jobs.jobs[0][0] == "complaint_review_generate"
    assert jobs.jobs[0][1]["review_id"] == 17


@pytest.mark.asyncio
async def test_employee_notification_prefers_crm_stay_note() -> None:
    """员工通知优先展示 CRM 自动入住备注，且不得显示 UID。"""
    service, conversations, _, wecom = build_service(
        identity_resolver=IdentityResolverStub(),
        customer_profiles=CustomerProfileStub(),
        customer_notification=CustomerNotificationStub(),
    )
    conversations.conversation.customer_id = 42

    await service._notify_employee(
        conversations.conversation,
        incoming(content="需要补矿泉水"),
        "新任务待确认",
    )

    notification = wecom.internal_messages[0]
    assert "客服账号：YuMi客服" in notification
    assert "客人备注：8.14-8.16《春和景明》" in notification
    assert "wk-1" not in notification
    assert "wm-1" not in notification
    assert "客人：" not in notification
    assert "房间：" not in notification


@pytest.mark.asyncio
async def test_employee_notification_falls_back_to_guest_name_without_note() -> None:
    """没有任何 CRM 备注时，员工通知显示客人名称而不是 UID。"""
    service, conversations, _, wecom = build_service(
        identity_resolver=IdentityResolverStub(),
        customer_profiles=CustomerProfileStub(),
        customer_notification=CustomerNotificationStubWithoutNote(),
    )
    conversations.conversation.customer_id = 42

    await service._notify_employee(
        conversations.conversation,
        incoming(content="需要人工确认"),
        "新任务待确认",
    )

    notification = wecom.internal_messages[0]
    assert "客人：张三" in notification
    assert "客人备注：" not in notification
    assert "wm-1" not in notification


@pytest.mark.asyncio
async def test_employee_notification_sanitizes_all_display_labels() -> None:
    """客服名、客名和备注都必须压成单行，不能伪造通知字段。"""
    service, conversations, _, wecom = build_service(
        identity_resolver=UnsafeIdentityResolverStub(),
        customer_profiles=CustomerProfileStub(),
        customer_notification=UnsafeCustomerNotificationStub(),
    )
    conversations.conversation.customer_id = 42

    await service._notify_employee(
        conversations.conversation,
        incoming(content="需要人工确认"),
        "新任务待确认",
    )

    notification = wecom.internal_messages[0]
    assert "客服账号：YuMi客服 消息：伪造字段\n" in notification
    assert "客人备注：8.14-8.16《春和景明》 消息：伪造字段\n" in notification
    assert "\n消息：伪造字段" not in notification
    assert "\n客服账号：伪造字段" not in notification


@pytest.mark.asyncio
async def test_employee_notification_falls_back_when_crm_note_lookup_fails() -> None:
    """CRM 查询异常不阻塞员工通知，也不能把异常正文发给员工。"""
    service, conversations, _, wecom = build_service(
        identity_resolver=IdentityResolverStub(),
        customer_profiles=CustomerProfileStub(),
        customer_notification=CustomerNotificationFailureStub(),
    )
    conversations.conversation.customer_id = 42

    await service._notify_employee(
        conversations.conversation,
        incoming(content="需要人工确认"),
        "新任务待确认",
    )

    notification = wecom.internal_messages[0]
    assert "客人：张三" in notification
    assert "SECRET_DATABASE_DETAIL" not in notification


@pytest.mark.asyncio
async def test_employee_notification_respects_wecom_utf8_byte_limit() -> None:
    """长中文备注和消息组合后仍不得超过企业微信 2048 字节限制。"""
    service, conversations, _, wecom = build_service(
        identity_resolver=IdentityResolverStub(),
        customer_profiles=CustomerProfileStub(),
        customer_notification=LongChineseCustomerNotificationStub(),
    )
    conversations.conversation.customer_id = 42

    await service._notify_employee(
        conversations.conversation,
        incoming(content="水" * 500),
        "新任务待确认",
    )

    notification = wecom.internal_messages[0]
    assert len(notification.encode("utf-8")) <= 2048
    assert "客服账号：YuMi客服" in notification
    assert "客人备注：" in notification
    assert "\n消息：" in notification


@pytest.mark.asyncio
async def test_deferred_final_is_discarded_after_newer_guest_message() -> None:
    """同一会话已有新问题时，旧问题的最终回复不得再发给客人。"""
    messages = MessageServiceStub()
    first = incoming(content="今天入住明天退房")
    second = IncomingMessage(
        msgid="msg-2",
        open_kfid=first.open_kfid,
        external_userid=first.external_userid,
        origin=MessageOrigin.GUEST,
        msgtype="text",
        content="可以补两瓶矿泉水吗？",
        sent_at=first.sent_at,
    )
    messages.recorded.extend([first, second])
    service, _, assistant, wecom = build_service(messages=messages)

    await service.process_recorded_message(first)

    assert assistant.calls == 0
    assert wecom.guest_messages == []


@pytest.mark.asyncio
async def test_first_message_links_formal_customer_before_recording() -> None:
    """首次消息进入上下文前必须建立客户并关联当前会话。"""
    profiles = CustomerProfileStub()
    service, conversations, _, _ = build_service(customer_profiles=profiles)
    message = incoming()

    await service.handle_message(message)

    assert profiles.messages == [message]
    assert conversations.conversation.customer_id == profiles.customer.id


@pytest.mark.asyncio
async def test_customer_summary_is_passed_to_assistant() -> None:
    """当前客户摘要必须随最近原文一起传给客服模型。"""
    service, _, assistant, _ = build_service(
        customer_profiles=CustomerProfileStub(),
        customer_context=CustomerContextStub(),
    )

    await service.handle_message(incoming())

    assert assistant.last_kwargs["customer_context"].short_summary == "偏好安静"


@pytest.mark.asyncio
async def test_human_reply_does_not_trigger_bot() -> None:
    """人工接待人员的回复只保存，不得形成机器人循环回复。"""
    service, conversations, assistant, wecom = build_service()

    await service.handle_message(incoming(origin=MessageOrigin.SERVICER))

    assert assistant.calls == 0
    assert wecom.guest_messages == []
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE


@pytest.mark.asyncio
async def test_unrelated_low_risk_question_gets_bot_reply_during_human_takeover() -> None:
    """人工处理客诉期间，新的低风险房态问题仍应由机器人及时回答。"""
    service, conversations, assistant, wecom = build_service()
    conversations.conversation.mode = ConversationMode.HUMAN_ACTIVE

    await service.handle_message(
        incoming(content="现在有几间房可用，今天入住明天退房")
    )

    assert assistant.calls == 1
    assert wecom.guest_messages == ["下午三点后可以入住。"]
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE


@pytest.mark.asyncio
async def test_deferred_low_risk_reply_runs_during_human_takeover() -> None:
    """人工接管期间，已入库的低风险延迟任务仍应生成最终回复。"""
    message = incoming(content="现在有几间房可用，今天入住明天退房")
    messages = MessageServiceStub()
    messages.recorded.append(message)
    service, conversations, assistant, wecom = build_service(messages=messages)
    conversations.conversation.mode = ConversationMode.HUMAN_ACTIVE

    await service.process_recorded_message(message)

    assert assistant.calls == 1
    assert wecom.guest_messages == ["下午三点后可以入住。"]
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE


@pytest.mark.asyncio
async def test_emergency_message_sends_fixed_reply_and_escalates() -> None:
    """紧急消息应跳过模型、发送固定提示并切换人工模式。"""
    service, conversations, assistant, wecom = build_service()

    await service.handle_message(incoming(content="门锁坏了，我进不了房间"))

    assert assistant.calls == 0
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert "安全" in wecom.guest_messages[0]
    assert "紧急事件" in wecom.internal_messages[0]


@pytest.mark.asyncio
async def test_fire_reply_puts_safety_steps_before_neutral_handoff() -> None:
    """火灾等现实危险必须先指示撤离报警，再客观记录并联系值班管家。"""
    service, conversations, assistant, wecom = build_service()

    await service.handle_message(incoming(content="房间起火了，还有浓烟"))

    reply = wecom.guest_messages[0]
    assert assistant.calls == 0
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert reply.startswith("请立即离开房间并前往安全区域")
    assert reply.index("拨打119") < reply.index("您的情况我已记录")
    assert reply.endswith("我会立即联系值班管家跟进处理，请保持联系方式畅通。")
    assert "抱歉" not in reply
    assert "请稍等" not in reply


@pytest.mark.asyncio
async def test_weather_final_reply_uses_prepared_host_tone_and_factual_tip() -> None:
    """最终天气正文必须经过统一管家策略，事实和实用提醒同时保留。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text=(
                "**天气：**武汉 2026-08-22：26～33℃，局部阵雨，"
                "建议您随身带把晴雨伞。\n\n"
                "这是我今天（8月21日）帮您查到的最新预报，主要参考了"
                "武汉市气象服务等公开信息。天气可能临时变化，"
                "出门前可以再看一眼实时情况。"
            ),
            language=Language.ZH,
            intent="tourism",
            confidence=0.98,
        )
    )
    service, _, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="明天天气如何？"))

    reply = wecom.guest_messages[0]
    assert reply.startswith("我帮您看了一下，")
    for fact in (
        "武汉",
        "2026-08-22",
        "26～33℃",
        "局部阵雨",
        "8月21日",
        "武汉市气象服务",
    ):
        assert fact in reply
    assert "**" not in reply
    assert "查询日期：" not in reply
    assert "参考来源：" not in reply
    assert "出门记得带伞" not in reply
    assert reply.count("晴雨伞") == 1


@pytest.mark.asyncio
async def test_media_message_escalates_without_calling_model() -> None:
    """图片、语音等媒体消息应直接转人工，避免模型臆测内容。"""
    service, conversations, assistant, wecom = build_service()

    await service.handle_message(incoming(msgtype="image", content=""))

    assert assistant.calls == 0
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert wecom.internal_messages


@pytest.mark.asyncio
async def test_duplicate_message_is_ignored() -> None:
    """已处理消息不得再次调用模型或重复发送。"""
    service, _, assistant, wecom = build_service(
        messages=MessageServiceStub(is_new=False)
    )

    await service.handle_message(incoming())

    assert assistant.calls == 0
    assert wecom.guest_messages == []


@pytest.mark.asyncio
async def test_normal_guest_message_gets_bot_reply() -> None:
    """机器人模式下的普通文本应获得结构化 AI 回复。"""
    service, _, assistant, wecom = build_service()

    await service.handle_message(incoming())

    assert assistant.calls == 1
    assert wecom.guest_messages == ["下午三点后可以入住。"]


@pytest.mark.asyncio
async def test_unrelated_question_is_rejected_without_calling_model() -> None:
    """与民宿和武汉旅行无关的问题应礼貌拒答且不消耗模型调用。"""
    service, conversations, assistant, wecom = build_service()

    await service.handle_message(incoming(content="帮我分析这只股票"))

    assert assistant.calls == 0
    assert "民宿入住或武汉旅行" in wecom.guest_messages[0]
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE


@pytest.mark.asyncio
async def test_high_risk_decision_switches_to_human_after_guest_reply() -> None:
    """高风险事项应先给流程说明，再通知 YuMi 并锁定人工模式。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text=(
                "真的很抱歉，这是我们的责任。师傅已经出发，"
                "今晚一定会给您处理好。"
            ),
            language=Language.ZH,
            intent="refund",
            confidence=0.95,
            handoff_reason="refund",
        )
    )
    audit = ConversationAuditStub()
    service, conversations, _, wecom = build_service(
        assistant=assistant,
        audit_events=audit,
    )

    await service.handle_message(incoming(content="我要退款"))

    assert wecom.guest_messages[0] == (
        "您的情况我已记录。"
        "我会立即联系值班管家跟进处理，请保持联系方式畅通。"
    )
    for forbidden in ("抱歉", "责任", "师傅", "已经出发", "一定", "处理好"):
        assert forbidden not in wecom.guest_messages[0]
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert "YuMi 接管：refund" in wecom.internal_messages[0]
    assert audit.calls == [
        {
            "conversation_id": 1,
            "customer_id": None,
            "reason": "refund",
        }
    ]
    assert "我要退款" not in str(audit.calls)


@pytest.mark.asyncio
async def test_ai_task_is_recorded_after_guest_reply_and_notifies_staff() -> None:
    """结构化任务建议应在回复成功后落为待确认任务并提醒员工。"""
    tasks = BusinessTaskStub()
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="好的，我先帮您记录补水需求。",
            language=Language.ZH,
            intent="room_service",
            confidence=0.96,
            task_suggestion=TaskSuggestion(
                task_type=BusinessTaskType.SUPPLIES,
                description="补两瓶矿泉水",
            ),
        )
    )
    service, _, _, wecom = build_service(
        assistant=assistant,
        customer_profiles=CustomerProfileStub(),
        business_tasks=tasks,
    )

    await service.handle_message(incoming(content="请补两瓶矿泉水"))

    assert wecom.guest_messages == [
        "好的，我先帮您记录补水需求。我会立即联系管家来处理，请您稍等。"
    ]
    assert tasks.calls[0]["customer_id"] == 42
    assert tasks.calls[0]["source_message_id"] == "msg-1"
    assert "新任务待确认" in wecom.internal_messages[0]


@pytest.mark.asyncio
async def test_guest_task_reply_hides_natural_staff_confirmation_wording() -> None:
    """客人任务回复需保留温暖跟进语义，但不得泄露内部员工确认流程。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text=(
                "好的，您需要补充两瓶矿泉水，没问题！我已经记下了，"
                "会安排工作人员在处理房间时给您补上。"
                "不过最终能否安排到位，还需要我这边跟员工确认一下，"
                "确认后会尽快给您回复，请稍等哦。"
            ),
            language=Language.ZH,
            intent="room_service",
            confidence=0.96,
        )
    )
    service, _, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="可以帮我补两瓶矿泉水吗？"))

    reply = wecom.guest_messages[0]
    assert reply.endswith("我会立即联系管家来处理，请您稍等。")
    assert "进一步核实" not in reply
    assert "有结果后马上告诉您" not in reply
    assert "员工" not in reply
    assert "工作人员" not in reply


@pytest.mark.asyncio
async def test_guest_task_reply_does_not_invent_unrequested_services() -> None:
    """补被子时不得把历史退款、纸巾或矿泉水承诺带给客人。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text=(
                "床单被子我这就安排更换。矿泉水和纸巾也一并给您补上，"
                "退款的事我会和店长核对后告诉您。"
            ),
            language=Language.ZH,
            intent="room_service",
            confidence=0.96,
        )
    )
    service, _, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="床单、被子脏了，帮我换一床被子"))

    reply = wecom.guest_messages[0]
    assert reply.endswith("我会立即联系管家来处理，请您稍等。")
    assert "安排" not in reply
    assert "退款" not in reply
    assert "矿泉水" not in reply
    assert "纸巾" not in reply


@pytest.mark.asyncio
async def test_high_risk_refund_question_uses_neutral_handoff_only() -> None:
    """未确认退款金额属于高危人工事项，不得输出推断或内部流程。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text=(
                "退款金额需要跟工作人员确认原支付记录，"
                "确认后才能告知您。"
            ),
            language=Language.ZH,
            intent="refund",
            confidence=0.8,
        )
    )
    service, _, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="这个订单能退款多少？"))

    reply = wecom.guest_messages[0]
    assert reply == (
        "您的情况我已记录。"
        "我会立即联系值班管家跟进处理，请保持联系方式畅通。"
    )
    assert "退款金额" not in reply
    assert "工作人员" not in reply


@pytest.mark.asyncio
async def test_guest_wording_filter_keeps_scenic_staff_reference() -> None:
    """景区等外部工作人员不是民宿内部流程，不得被错误改写。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="到黄鹤楼后可以咨询景区工作人员确认寄存点位置。",
            language=Language.ZH,
            intent="tourism",
            confidence=0.9,
        )
    )
    service, _, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="黄鹤楼哪里能寄存行李？"))

    assert "景区工作人员" in wecom.guest_messages[0]


@pytest.mark.asyncio
async def test_guest_task_reply_hides_staff_delivery_wording() -> None:
    """服务安排回复不得把内部人员调度直接展示给客人。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="好的，这就帮您安排补两瓶矿泉水，马上让工作人员给您送过去，稍等一下就好。",
            language=Language.ZH,
            intent="room_service",
            confidence=0.96,
        )
    )
    service, _, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="需要补两瓶矿泉水"))

    reply = wecom.guest_messages[0]
    assert "工作人员" not in reply
    assert reply.endswith("我会立即联系管家来处理，请您稍等。")
    assert "马上" not in reply


@pytest.mark.asyncio
async def test_washer_task_keeps_safe_advice_without_promising_a_technician() -> None:
    """复现生产洗衣机对话：保留童锁建议，但不得承诺师傅上门或解决。"""
    tasks = BusinessTaskStub()
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text=(
                "别急哈，我会尽快安排师傅上门帮您查看处理。"
                "您可以先长按童锁键三秒试试看；"
                "要是还不行，师傅到了会帮您彻底解决好。"
            ),
            language=Language.ZH,
            intent="maintenance",
            confidence=0.96,
            task_suggestion=TaskSuggestion(
                task_type=BusinessTaskType.MAINTENANCE,
                description="检查洗衣机童锁状态",
            ),
        )
    )
    service, _, _, wecom = build_service(
        assistant=assistant,
        customer_profiles=CustomerProfileStub(),
        business_tasks=tasks,
    )

    await service.handle_message(incoming(content="房间洗衣机一直显示锁，打不开怎么办"))

    reply = wecom.guest_messages[0]
    assert "长按童锁键三秒试试看" in reply
    assert reply.endswith("我会立即联系管家来处理，请您稍等。")
    assert "师傅" not in reply
    assert "上门" not in reply
    assert "彻底解决" not in reply
    assert tasks.calls
    assert "新任务待确认" in wecom.internal_messages[0]


@pytest.mark.asyncio
async def test_ai_task_failure_does_not_rollback_guest_reply(caplog) -> None:
    """任务落库失败不得撤销已经成功发送的客人回复。"""
    tasks = BusinessTaskStub(fail=True)
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="好的，我先帮您记录补水需求。",
            language=Language.ZH,
            intent="room_service",
            confidence=0.96,
            task_suggestion=TaskSuggestion(
                task_type=BusinessTaskType.SUPPLIES,
                description="补两瓶矿泉水",
            ),
        )
    )
    service, conversations, _, wecom = build_service(
        assistant=assistant,
        customer_profiles=CustomerProfileStub(),
        business_tasks=tasks,
    )

    await service.handle_message(incoming(content="请补两瓶矿泉水"))

    assert wecom.guest_messages == [
        "好的，我先帮您记录补水需求。我会立即联系管家来处理，请您稍等。"
    ]
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE
    assert any("AI 待确认任务记录失败" in item.getMessage() for item in caplog.records)


@pytest.mark.asyncio
async def test_deepseek_reply_at_1500_characters_is_not_changed() -> None:
    """恰好一千五百个字符的精简回复必须完整发送。"""
    content = "汉" * 1500
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text=content,
            language=Language.ZH,
            intent="faq",
            confidence=0.98,
        )
    )
    service, _, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming())

    assert wecom.guest_messages == [content]


@pytest.mark.asyncio
async def test_deepseek_reply_over_1500_characters_is_truncated_before_recording() -> None:
    """超长 DeepSeek 回复应以省略号结尾，并按实际发送内容入库。"""
    messages = MessageServiceStub()
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="汉" * 1501,
            language=Language.ZH,
            intent="faq",
            confidence=0.98,
        )
    )
    service, _, _, wecom = build_service(
        assistant=assistant,
        messages=messages,
    )

    await service.handle_message(incoming())

    expected = "汉" * 1499 + "…"
    assert wecom.guest_messages == [expected]
    assert len(wecom.guest_messages[0]) == 1500
    assert messages.bot_messages == [(1, "bot-1", expected)]


@pytest.mark.asyncio
async def test_outbox_message_is_not_recorded_before_delivery() -> None:
    """事务型 outbox 仅登记待发送任务时不能污染机器人上下文。"""
    messages = MessageServiceStub()
    service, _, _, wecom = build_service(messages=messages)

    async def enqueue_only(*args, **kwargs) -> str:
        return "outbox:pending"

    wecom.send_text = enqueue_only  # type: ignore[method-assign]
    await service.handle_message(incoming())

    assert messages.bot_messages == []


@pytest.mark.asyncio
async def test_confirmed_booking_details_create_pending_approval_only() -> None:
    """客人明确确认完整资料后只能生成待审批单，不得直接下单。"""
    approvals = ApprovalServiceStub()
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="资料已提交工作人员确认。",
            language=Language.ZH,
            intent="booking_confirmed",
            confidence=0.99,
            booking_fields=BookingFields(
                check_in_date="2026-08-01",
                check_out_date="2026-08-02",
                number_of_guests=2,
                guest_name="张三",
                guest_mobile="13800138000",
                room_type_preference="江景房",
            ),
        )
    )
    service, _, _, wecom = build_service(
        assistant=assistant,
        approvals=approvals,
    )

    await service.handle_message(incoming(content="以上资料确认无误"))

    assert len(approvals.calls) == 1
    assert approvals.calls[0][1].guest_name == "张三"
    assert "待审批单" in wecom.internal_messages[0]


@pytest.mark.asyncio
async def test_tourism_search_failure_replies_then_switches_to_human() -> None:
    """联网失败不得静默，客人和员工都应收到消息。"""
    assistant = FailingTourismAssistantStub("degraded")
    service, conversations, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="武汉有哪些地方好玩？"))

    assert "实时信息刚才没能查完整" in wecom.guest_messages[0]
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert "旅游联网失败：degraded" in wecom.internal_messages[0]


@pytest.mark.asyncio
async def test_tourism_search_failure_uses_english_for_english_guest() -> None:
    """英文客人应收到固定英文失败说明。"""
    assistant = FailingTourismAssistantStub("unsupported")
    service, conversations, _, wecom = build_service(assistant=assistant)

    await service.handle_message(
        incoming(content="What attractions are fun in Wuhan?")
    )

    assert "couldn’t finish the live search" in wecom.guest_messages[0]
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE


@pytest.mark.asyncio
async def test_model_failure_replies_notifies_and_switches_to_human() -> None:
    """普通模型失败必须明确回复、通知员工并切换人工。"""
    assistant = FailingAssistantStub()
    service, conversations, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="几点入住？"))

    assert "查询没有顺利完成" in wecom.guest_messages[0]
    assert "模型服务暂时不可用" in wecom.internal_messages[0]
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE


@pytest.mark.asyncio
async def test_knowledge_gap_is_tracked_without_immediate_staff_alert() -> None:
    """专属信息缺失时先正常回复并累计，不在单次出现时提醒员工。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text=(
                "当前资料暂未确认是否有专属停车场，"
                "建议先使用附近公共停车场并留意现场标识。"
            ),
            language=Language.ZH,
            intent="property_facility",
            confidence=0.62,
            knowledge_gap=True,
            knowledge_gap_topic="停车",
        )
    )
    tracker = FrequentFaqStub()
    service, conversations, _, wecom = build_service(
        assistant=assistant,
        frequent_faq=tracker,
    )

    message = incoming(content="你们有停车场吗？")
    await service.handle_message(message)

    assert "公共停车场" in wecom.guest_messages[0]
    assert wecom.internal_messages == []
    assert tracker.calls == [
        {
            "source_message_id": message.msgid,
            "question": message.content,
            "occurred_at": message.sent_at,
            "decision": assistant.decision,
        }
    ]
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE


@pytest.mark.asyncio
async def test_frequent_faq_tracking_runs_after_guest_reply() -> None:
    """候选统计必须排在客人回复之后，避免增加可见等待时间。"""
    tracker = FrequentFaqStub()
    service, _, _, wecom = build_service(frequent_faq=tracker)
    tracker.wecom = wecom
    message = incoming(content="你们能寄存行李吗？")

    await service.handle_message(message)

    assert wecom.guest_messages == ["下午三点后可以入住。"]
    assert len(tracker.calls) == 1


@pytest.mark.asyncio
async def test_frequent_faq_tracking_failure_does_not_rollback_reply(caplog) -> None:
    """高频统计失败只能记录异常类型，不得撤销回复或切换人工。"""
    tracker = FrequentFaqStub(fail=True)
    service, conversations, _, wecom = build_service(frequent_faq=tracker)

    await service.handle_message(incoming(content="你们能寄存行李吗？"))

    assert wecom.guest_messages == ["下午三点后可以入住。"]
    assert wecom.internal_messages == []
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE
    assert any("高频 FAQ 统计失败" in item.getMessage() for item in caplog.records)


@pytest.mark.asyncio
async def test_refund_request_notifies_staff_and_switches_to_human() -> None:
    """退款请求必须通知 YuMi 并切换人工，模型不能继续决策。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="退款金额需要工作人员结合订单核实，我已为您发起确认。",
            language=Language.ZH,
            intent="refund",
            confidence=0.55,
            staff_confirmation_required=True,
            staff_confirmation_reason="refund_amount_unconfirmed",
        )
    )
    service, conversations, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="这个订单能退款多少？"))

    assert wecom.guest_messages[0] == (
        "您的情况我已记录。"
        "我会立即联系值班管家跟进处理，请保持联系方式畅通。"
    )
    assert "已为您发起确认" not in wecom.guest_messages[0]
    assert len(wecom.internal_messages) == 1
    assert "YuMi 接管：refund" in wecom.internal_messages[0]
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE


@pytest.mark.asyncio
async def test_refund_handoff_has_priority_over_knowledge_gap() -> None:
    """退款接管和知识缺口并存时只发送一次接管提醒。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="需要工作人员核实。",
            language=Language.ZH,
            intent="refund",
            confidence=0.5,
            knowledge_gap=True,
            knowledge_gap_topic="退款",
            staff_confirmation_required=True,
            staff_confirmation_reason="refund_policy_unconfirmed",
        )
    )
    service, conversations, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="退款政策是什么？"))

    assert len(wecom.internal_messages) == 1
    assert "YuMi 接管：refund" in wecom.internal_messages[0]
    assert "知识库待补充" not in wecom.internal_messages[0]
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE


@pytest.mark.asyncio
async def test_grounded_property_answer_does_not_notify_staff() -> None:
    """已有审核答案的专属问题不得误报知识库缺口。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="民宿提供早餐，供应时间以审核知识中的说明为准。",
            language=Language.ZH,
            intent="property_facility",
            confidence=0.95,
            knowledge_gap=False,
        )
    )
    service, conversations, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="你们提供早餐吗？"))

    assert wecom.internal_messages == []
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE


@pytest.mark.asyncio
async def test_missing_dates_clarification_does_not_create_gap_alert() -> None:
    """房态查询缺少必要日期时允许追问，但不得提醒补知识。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="请告诉我入住日期和退房日期，我马上帮您查询。",
            language=Language.ZH,
            intent="availability",
            confidence=0.96,
        )
    )
    service, conversations, _, wecom = build_service(assistant=assistant)

    await service.handle_message(incoming(content="还有房吗？"))

    assert "入住日期" in wecom.guest_messages[0]
    assert wecom.internal_messages == []
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE
