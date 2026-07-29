from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import ConversationMode, Language, MessageOrigin
from homestay_bot.domain.models import Conversation
from homestay_bot.integrations.openai_client import AssistantDecision, BookingFields
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
        self, conversation_id: int, message_id: str, content: str
    ) -> None:
        """记录机器人出站消息。"""
        self.bot_messages.append((conversation_id, message_id, content))

    async def build_context(
        self, conversation_id: int, limit: int = 20
    ) -> list[dict[str, str]]:
        """把测试中已记录的客人文本转换为模型历史。"""
        return [
            {"role": "user", "content": item.content}
            for item in self.recorded[-limit:]
            if item.origin is MessageOrigin.GUEST and item.msgtype == "text"
        ]


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

    async def respond(self, **kwargs) -> AssistantDecision:
        """生成固定中文回复。"""
        self.calls += 1
        if self.decision is not None:
            return self.decision
        return AssistantDecision(
            reply_text="下午三点后可以入住。",
            language=Language.ZH,
            intent="faq",
            confidence=0.98,
            handoff_reason=self.handoff_reason,
        )


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


class ApprovalServiceStub:
    """记录完整预订资料只创建待审批单。"""

    def __init__(self) -> None:
        self.calls = []

    async def create_pending(self, conversation_id, request):
        """记录会话和预订资料，不调用任何百居易写接口。"""
        self.calls.append((conversation_id, request))
        return SimpleNamespace(id=9, approval_code="APP-9")


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
    )
    return service, conversations, selected_assistant, wecom


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
