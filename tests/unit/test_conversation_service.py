from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import BusinessTaskType, ConversationMode, Language, MessageOrigin
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
from homestay_bot.services.context_retention import CustomerModelContext
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.message_service import IncomingMessage


class ConversationRepositoryStub:
    """在内存中维护单个会话。"""

    def __init__(self) -> None:
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


class MessageServiceStub:
    """记录消息并支持模拟重复消息。"""

    def __init__(self, *, is_new: bool = True) -> None:
        self.is_new = is_new
        self.recorded: list[IncomingMessage] = []
        self.bot_messages: list[tuple[int, str, str]] = []

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
        return [
            {"role": "user", "content": item.content}
            for item in recorded[-limit:]
            if item.origin is MessageOrigin.GUEST and item.msgtype == "text"
        ]

    async def has_newer_guest_message(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源消息之后是否已有更新客人消息。"""
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

    async def load_model_context(self, customer_id: int) -> CustomerModelContext:
        """验证按正式客户主键读取摘要。"""
        assert customer_id == 42
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
        self.handoff_reason = handoff_reason
        self.decision = decision
        self.last_kwargs = None

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


class WeComStub:
    """记录发送给客人及值班员工的消息。"""

    def __init__(self) -> None:
        self.guest_messages: list[str] = []
        self.internal_messages: list[str] = []

    async def send_text(
        self, open_kfid: str, external_userid: str, content: str
    ) -> str:
        """记录客人消息并返回企业微信消息编号。"""
        self.guest_messages.append(content)
        return f"bot-{len(self.guest_messages)}"

    async def send_internal_text(
        self, *, agent_id: int, employee_userids: list[str], content: str
    ) -> None:
        """记录内部升级通知。"""
        self.internal_messages.append(content)


class DeferredJobStub:
    """记录快速安抚阶段登记的最终处理任务。"""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, object], str | None]] = []

    async def enqueue(self, job_type, payload, *, dedupe_key=None):
        """保存任务类型和稳定去重键。"""
        self.jobs.append((job_type, payload, dedupe_key))
        return SimpleNamespace()


class ApprovalServiceStub:
    """记录完整预订资料只创建待审批单。"""

    def __init__(self) -> None:
        self.calls = []

    async def create_pending(self, conversation_id, request):
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
    messages: MessageServiceStub | None = None,
    approvals: ApprovalServiceStub | None = None,
    frequent_faq: FrequentFaqStub | None = None,
    customer_profiles: CustomerProfileStub | None = None,
    customer_context: CustomerContextStub | None = None,
    business_tasks=None,
    audit_events=None,
    jobs=None,
    defer_model: bool = False,
    commit_boundary=None,
) -> tuple[ConversationService, ConversationRepositoryStub, AssistantStub, WeComStub]:
    """创建注入固定依赖的会话服务。"""
    conversations = ConversationRepositoryStub()
    selected_assistant = assistant or AssistantStub()
    wecom = WeComStub()
    service = ConversationService(
        conversations=conversations,
        messages=messages or MessageServiceStub(),
        assistant=selected_assistant,
        emergency_service=EmergencyService(),
        wecom=wecom,
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
    )
    return service, conversations, selected_assistant, wecom


@pytest.mark.asyncio
async def test_deferred_message_sends_model_ack_and_enqueues_final_task() -> None:
    """阶段一应先发送模型安抚，再登记最终处理任务。"""
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

    assert assistant.calls == 0
    assert wecom.guest_messages == ["收到啦，我来帮您看看。"]
    assert jobs.jobs[0][0] == "wecom_process_message"
    assert jobs.jobs[0][2] == "final:msg-1"
    assert commits == 1


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
async def test_emergency_message_sends_fixed_reply_and_escalates() -> None:
    """紧急消息应跳过模型、发送固定提示并切换人工模式。"""
    service, conversations, assistant, wecom = build_service()

    await service.handle_message(incoming(content="门锁坏了，我进不了房间"))

    assert assistant.calls == 0
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert "安全" in wecom.guest_messages[0]
    assert "紧急事件" in wecom.internal_messages[0]


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
            reply_text="退款需结合订单由工作人员核实，我已通知工作人员。",
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

    assert "工作人员" in wecom.guest_messages[0]
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

    assert wecom.guest_messages == ["好的，我先帮您记录补水需求。"]
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
    assert "两瓶矿泉水" in reply
    assert "进一步核实" in reply
    assert "有结果后马上告诉您" in reply
    assert "员工" not in reply
    assert "工作人员" not in reply


@pytest.mark.asyncio
async def test_guest_wording_filter_preserves_refund_facts() -> None:
    """退款回复可隐藏内部角色，但不得删除金额核实对象和不确定性。"""
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
    assert "退款金额" in reply
    assert "原支付记录" in reply
    assert "进一步核实" in reply


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

    assert wecom.guest_messages == ["好的，我先帮您记录补水需求。"]
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

    assert "已为您发起确认" in wecom.guest_messages[0]
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
