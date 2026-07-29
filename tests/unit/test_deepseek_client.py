import json
import re
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


class LongTourismStub:
    """返回超过精简阈值且带来源信息的旅游回复。"""

    async def search(self, **kwargs) -> str:
        """提供固定长回复用于验证旅游精简路径。"""
        return (
            "武汉旅游建议。" * 180
            + "\n查询日期：2026-07-30"
            + "\n参考来源：武汉市文化和旅游局"
        )


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
async def test_long_general_reply_is_semantically_refined_once() -> None:
    """超过一千字的普通回复必须再调用一次 DeepSeek 精简选优。"""
    long_reply = "需要保留的原始内容。" * 120
    payload = decision_payload()
    payload["reply_text"] = long_reply
    refined_reply = "精简后的完整重点，保留关键事实和必要提示。"
    client = ChatClientStub(
        [
            json.dumps(payload, ensure_ascii=False),
            json.dumps({"reply_text": refined_reply}, ensure_ascii=False),
        ]
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
        messages=[{"role": "user", "content": "请详细说明。"}],
    )

    assert decision.reply_text == refined_reply
    assert len(client.chat.completions.requests) == 2
    refinement_request = client.chat.completions.requests[1]
    assert "tools" not in refinement_request
    assert long_reply in refinement_request["messages"][1]["content"]
    refinement_prompt = refinement_request["messages"][0]["content"]
    assert "不得新增事实" in refinement_prompt
    assert "不得添加链接" in refinement_prompt
    assert "1000" in refinement_prompt


@pytest.mark.asyncio
async def test_long_tourism_reply_is_refined_and_keeps_evidence_labels() -> None:
    """超长旅游回复也要精简，并保留查询日期和来源名称。"""
    refined_reply = (
        "推荐东湖和湖北省博物馆。\n"
        "查询日期：2026-07-30\n"
        "参考来源：武汉市文化和旅游局"
    )
    client = ChatClientStub(
        [json.dumps({"reply_text": refined_reply}, ensure_ascii=False)]
    )
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=LongTourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "武汉最近有什么好玩的？"}],
    )

    assert decision.reply_text == refined_reply
    assert "查询日期：2026-07-30" in decision.reply_text
    assert "参考来源：武汉市文化和旅游局" in decision.reply_text
    assert len(client.chat.completions.requests) == 1


@pytest.mark.asyncio
async def test_refinement_failure_keeps_original_reply_for_hard_limit_fallback(
    caplog,
) -> None:
    """精简响应无效时不得丢失原回复，应交由发送层执行硬上限。"""
    long_reply = "原始完整回复。" * 180
    payload = decision_payload()
    payload["reply_text"] = long_reply
    client = ChatClientStub(
        [
            json.dumps(payload, ensure_ascii=False),
            "{}",
        ]
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
        messages=[{"role": "user", "content": "请详细说明。"}],
    )

    assert decision.reply_text == long_reply
    assert len(client.chat.completions.requests) == 2
    assert any(
        "DeepSeek 回复精简失败" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_unsafe_tourism_refinement_falls_back_to_original_evidence() -> None:
    """新增链接或丢失来源标签的精简结果必须被拒绝。"""
    unsafe_reply = "精简推荐：https://example.com"
    client = ChatClientStub(
        [json.dumps({"reply_text": unsafe_reply}, ensure_ascii=False)]
    )
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=LongTourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "武汉最近有什么好玩的？"}],
    )

    assert decision.reply_text != unsafe_reply
    assert "查询日期：2026-07-30" in decision.reply_text
    assert "参考来源：武汉市文化和旅游局" in decision.reply_text
    assert "https://" not in decision.reply_text


@pytest.mark.asyncio
async def test_refinement_cannot_reintroduce_property_hallucination() -> None:
    """二次精简不得重新添加未经审核的本店设施事实。"""
    long_reply = "建议先统一预算、分工和每日重点安排。" * 100
    payload = decision_payload()
    payload["reply_text"] = long_reply
    client = ChatClientStub(
        [
            json.dumps(payload, ensure_ascii=False),
            json.dumps(
                {"reply_text": "我们民宿有泳池，适合大家一起放松。"},
                ensure_ascii=False,
            ),
        ]
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
        messages=[{"role": "user", "content": "怎样和朋友协调旅行安排？"}],
    )

    assert "泳池" not in decision.reply_text
    assert "我们民宿" not in decision.reply_text


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
async def test_general_reply_removes_ungrounded_property_promotion() -> None:
    """普通回答不得夹带未经审核的民宿房型或设施宣传。"""
    payload = decision_payload()
    payload["reply_text"] = (
        "1. 建立共享文档，统一记录预算和行程。\n"
        "2. 比如我们民宿有7间不同风格的房型，可以一起选。\n"
        "3. 如果住我们民宿，客厅和庭院适合晚上复盘行程。\n"
        "4. 每天只安排一两个核心活动，并预留机动时间。"
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

    assert "共享文档" in decision.reply_text
    assert "机动时间" in decision.reply_text
    assert "不同风格" not in decision.reply_text
    assert "客厅" not in decision.reply_text
    assert "庭院" not in decision.reply_text


@pytest.mark.asyncio
async def test_property_filter_renumbers_list_and_removes_room_sales_cta() -> None:
    """删除专属宣传后应连续编号，并清理无关房型推销。"""
    payload = decision_payload()
    payload["reply_text"] = (
        "建议这样协调：\n"
        "1. 建立共享文档。\n"
        "2. 我们民宿有不同风格房型。\n"
        "3. 分工查询交通和景点。\n"
        "4. 每天预留机动时间。\n"
        "5. 如果住我们民宿，可以使用客厅和庭院。\n"
        "6. 行程不一致时可以灵活分组。\n"
        "如果您需要，我也可以推荐适合朋友一起住的房型。"
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

    numbered_lines = [
        line for line in decision.reply_text.splitlines()
        if re.match(r"^\d+\.", line)
    ]
    assert numbered_lines == [
        "1. 建立共享文档。",
        "2. 分工查询交通和景点。",
        "3. 每天预留机动时间。",
        "4. 行程不一致时可以灵活分组。",
    ]
    assert "房型" not in decision.reply_text
    assert "客厅" not in decision.reply_text
    assert "庭院" not in decision.reply_text


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
