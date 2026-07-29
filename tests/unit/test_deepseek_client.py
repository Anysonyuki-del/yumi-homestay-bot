import json
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import (
    AssistantUnavailableError,
    DeepSeekGuestAssistant,
)
from homestay_bot.services.knowledge_service import KnowledgeSnippet


class KnowledgeStub:
    """返回固定审核知识。"""

    async def build_context(self, language: Language) -> list[KnowledgeSnippet]:
        """提供入住知识用于构造系统提示。"""
        return [
            KnowledgeSnippet(
                source_id=1,
                category="入住",
                question="几点入住？",
                answer="下午三点后入住。",
            )
        ]


class TourismStub:
    """普通客服测试不应调用旅游搜索。"""

    async def search(self, **kwargs) -> str:
        """意外调用时让测试立即失败。"""
        raise AssertionError("普通问题不应调用旅游搜索")


def decision_payload() -> dict[str, object]:
    """返回完整、严格的客服决定。"""
    return {
        "reply_text": "下午三点后可以入住。",
        "language": "zh",
        "intent": "faq",
        "confidence": 0.98,
        "handoff_reason": None,
        "booking_fields": None,
        "knowledge_gap": False,
        "knowledge_gap_topic": None,
        "staff_confirmation_required": False,
        "staff_confirmation_reason": None,
    }


class CompletionsStub:
    """记录 Chat Completions 请求并按顺序返回内容。"""

    def __init__(self, contents: list[str]) -> None:
        """保存每次请求应返回的文本。"""
        self.contents = contents
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        """返回无工具调用的 Chat Completion。"""
        self.requests.append(kwargs)
        content = self.contents[min(len(self.requests) - 1, len(self.contents) - 1)]
        message = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ChatClientStub:
    """模拟 OpenAI SDK 的 chat.completions 资源。"""

    def __init__(self, contents: list[str]) -> None:
        """暴露可记录请求的 completions。"""
        self.chat = SimpleNamespace(completions=CompletionsStub(contents))


class ToolCompletionsStub:
    """先请求房态工具，再返回最终 JSON。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        """按调用轮次返回工具调用或最终决定。"""
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            function = SimpleNamespace(
                name="search_availability",
                arguments=(
                    '{"check_in_date":"2026-07-30",'
                    '"check_out_date":"2026-07-31"}'
                ),
            )
            call = SimpleNamespace(
                id="call-1",
                type="function",
                function=function,
            )
            message = SimpleNamespace(
                content=None,
                tool_calls=[call],
                model_dump=lambda **kwargs: {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": function.name,
                                "arguments": function.arguments,
                            },
                        }
                    ],
                },
            )
        else:
            message = SimpleNamespace(
                content=json.dumps(decision_payload(), ensure_ascii=False),
                tool_calls=None,
            )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ToolClientStub:
    """暴露工具调用 Chat Completions。"""

    def __init__(self) -> None:
        """初始化工具请求资源。"""
        self.chat = SimpleNamespace(completions=ToolCompletionsStub())


class ToolExecutorStub:
    """记录模型提出的只读工具调用。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def execute(self, name: str, arguments: dict[str, str]) -> dict[str, object]:
        """返回固定房态。"""
        self.calls.append((name, arguments))
        return {"available": True, "rooms": 1}


@pytest.mark.asyncio
async def test_deepseek_chat_returns_structured_decision_without_raw_guest_id() -> None:
    """普通客服必须使用 JSON Output，且不发送企业微信原始用户 ID。"""
    client = ChatClientStub([json.dumps(decision_payload(), ensure_ascii=False)])
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    decision = await assistant.respond(
        guest_identifier="wm-sensitive-id",
        language=Language.ZH,
        messages=[{"role": "user", "content": "几点入住？"}],
    )

    request = client.chat.completions.requests[0]
    assert decision.reply_text == "下午三点后可以入住。"
    assert request["model"] == "deepseek-v4-flash"
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "wm-sensitive-id" not in json.dumps(request, ensure_ascii=False)


@pytest.mark.asyncio
async def test_empty_json_response_retries_once() -> None:
    """DeepSeek 首次返回空 JSON 时只重试一次。"""
    client = ChatClientStub(
        ["", json.dumps(decision_payload(), ensure_ascii=False)]
    )
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "几点入住？"}],
    )

    assert decision.intent == "faq"
    assert len(client.chat.completions.requests) == 2


@pytest.mark.asyncio
async def test_two_invalid_json_responses_raise_unavailable(caplog) -> None:
    """连续两次无效结构化输出必须进入统一失败边界。"""
    client = ChatClientStub(["", "不是 JSON"])
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    with pytest.raises(AssistantUnavailableError):
        await assistant.respond(
            guest_identifier="wm-guest",
            language=Language.ZH,
            messages=[{"role": "user", "content": "几点入住？"}],
        )

    assert len(client.chat.completions.requests) == 2
    assert sum(
        "DeepSeek 对话调用失败" in record.getMessage()
        for record in caplog.records
    ) == 2
    assert all(
        "不是 JSON" not in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_deepseek_executes_read_only_tool_and_replays_result() -> None:
    """Chat Completions 工具调用必须执行并回传结果。"""
    client = ToolClientStub()
    executor = ToolExecutorStub()
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
        tool_executor=executor,
    )

    await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "今天入住明天退房还有房吗？"}],
    )

    assert executor.calls == [
        (
            "search_availability",
            {
                "check_in_date": "2026-07-30",
                "check_out_date": "2026-07-31",
            },
        )
    ]
    assert client.chat.completions.requests[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"available": true, "rooms": 1}',
    }
    assert client.chat.completions.requests[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "search_availability"},
    }


@pytest.mark.asyncio
async def test_general_question_clears_model_knowledge_gap_mistake() -> None:
    """普通常识问题不得因模型误标而提醒补知识库。"""
    payload = decision_payload()
    payload.update(
        {
            "confidence": 0.6,
            "knowledge_gap": True,
            "knowledge_gap_topic": "旅行协调",
        }
    )
    client = ChatClientStub([json.dumps(payload, ensure_ascii=False)])
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "怎样和朋友协调旅行安排？"}],
    )

    assert decision.knowledge_gap is False
    assert decision.knowledge_gap_topic is None


@pytest.mark.asyncio
async def test_previous_assistant_failure_reply_is_excluded_from_model_context() -> None:
    """固定失败文案不得污染后续 DeepSeek 对话上下文。"""
    client = ChatClientStub([json.dumps(decision_payload(), ensure_ascii=False)])
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[
            {"role": "user", "content": "上一个问题"},
            {
                "role": "assistant",
                "content": "暂时无法处理这个问题，已为您通知工作人员协助，请稍候。",
            },
            {"role": "user", "content": "怎样和朋友协调旅行安排？"},
        ],
    )

    request_messages = client.chat.completions.requests[0]["messages"]
    assert all(
        message.get("content")
        != "暂时无法处理这个问题，已为您通知工作人员协助，请稍候。"
        for message in request_messages
    )
    assert all(
        message.get("content") != "上一个问题"
        for message in request_messages
    )


@pytest.mark.asyncio
async def test_deepseek_context_keeps_only_five_latest_valid_messages() -> None:
    """DeepSeek 结构化对话只携带最近五条有效消息。"""
    client = ChatClientStub([json.dumps(decision_payload(), ensure_ascii=False)])
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "第二条"},
            {"role": "user", "content": "第三条"},
            {"role": "assistant", "content": "第四条"},
            {"role": "user", "content": "第五条"},
            {"role": "assistant", "content": "第六条"},
            {"role": "user", "content": "怎样和朋友协调旅行安排？"},
        ],
    )

    context = client.chat.completions.requests[0]["messages"][1:]
    assert len(context) == 5
    assert [message["content"] for message in context] == [
        "第三条",
        "第四条",
        "第五条",
        "第六条",
        "怎样和朋友协调旅行安排？",
    ]


@pytest.mark.asyncio
async def test_ungrounded_property_claim_is_forced_to_knowledge_gap() -> None:
    """审核知识未包含停车时，模型高置信度回答也必须标记缺口。"""
    payload = decision_payload()
    payload.update(
        {
            "reply_text": "我们提供免费停车位。",
            "confidence": 0.9,
            "knowledge_gap": False,
        }
    )
    client = ChatClientStub([json.dumps(payload, ensure_ascii=False)])
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "你们有停车场吗？"}],
    )

    assert decision.knowledge_gap is True
    assert decision.knowledge_gap_topic == "property_information"
