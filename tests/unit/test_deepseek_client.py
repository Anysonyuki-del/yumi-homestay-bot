import json
import re
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import BusinessTaskType, Language
from homestay_bot.integrations.deepseek_client import (
    AssistantUnavailableError,
    DeepSeekGuestAssistant,
)
from homestay_bot.services.context_retention import CustomerModelContext
from homestay_bot.services.faq_candidate_context import (
    FaqCandidateContextService,
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


class ParkingKnowledgeStub:
    """返回已经覆盖停车主题的审核知识。"""

    async def build_context(self, language: Language) -> list[KnowledgeSnippet]:
        """提供停车知识用于验证已覆盖主题不会进入候选。"""
        return [
            KnowledgeSnippet(
                source_id=2,
                category="停车",
                question="民宿有停车位吗？",
                answer="停车安排请按审核说明执行。",
            )
        ]


class CandidateRepositoryStub:
    """返回含隐私附属字段的候选，验证模型上下文只取必要内容。"""

    def __init__(self, count: int = 55) -> None:
        """构造指定数量的未关闭候选。"""
        self.items = [
            SimpleNamespace(
                id=index,
                canonical_question=f"标准问题{index}",
                examples=[f"客人原始问法{index}"],
                external_userid=f"wm-sensitive-{index}",
            )
            for index in range(1, count + 1)
        ]

    async def list_context(self, *, now):
        """返回候选列表，时间参数由服务负责提供。"""
        return self.items


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
async def test_customer_summary_is_added_without_raw_customer_identity() -> None:
    """客服请求应携带脱敏客户摘要，不携带原始企业微信身份。"""
    client = ChatClientStub([json.dumps(decision_payload(), ensure_ascii=False)])
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    await assistant.respond(
        guest_identifier="wm-sensitive-id",
        language=Language.ZH,
        messages=[{"role": "user", "content": "还是想要安静的房间"}],
        customer_context=CustomerModelContext(
            short_summary="偏好安静房间",
            long_summary="曾经入住过",
            unresolved_items=["停车方式待确认"],
        ),
    )

    request_text = json.dumps(
        client.chat.completions.requests[0],
        ensure_ascii=False,
    )
    assert "偏好安静房间" in request_text
    assert "曾经入住过" in request_text
    assert "停车方式待确认" in request_text
    assert "wm-sensitive-id" not in request_text


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
async def test_task_suggestion_is_returned_in_same_structured_response() -> None:
    """模型应在同一轮回复中返回可选的待确认任务建议。"""
    payload = decision_payload()
    payload["task_suggestion"] = {
        "task_type": "supplies",
        "description": "请补两瓶矿泉水",
        "property_id": 101,
        "service_date": "2026-08-01",
    }
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
        messages=[{"role": "user", "content": "请给101房补两瓶水"}],
    )

    assert decision.task_suggestion is not None
    assert decision.task_suggestion.task_type is BusinessTaskType.SUPPLIES
    assert decision.task_suggestion.property_id == 101


@pytest.mark.asyncio
async def test_system_only_task_suggestion_is_removed_locally() -> None:
    """模型不得通过结构化输出创建系统专用人工联系任务。"""
    payload = decision_payload()
    payload["task_suggestion"] = {
        "task_type": "manual_contact",
        "description": "联系客人，手机号13800138000",
        "property_id": None,
        "service_date": None,
    }
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
        messages=[{"role": "user", "content": "联系我"}],
    )

    assert decision.task_suggestion is None
    schema_prompt = client.chat.completions.requests[0]["messages"][0]["content"]
    assert '"manual_contact"' not in schema_prompt


@pytest.mark.asyncio
async def test_early_check_in_is_forced_to_human_handoff() -> None:
    """提前入住即使模型未标记，也必须由本地规则要求 YuMi 接管。"""
    payload = decision_payload()
    payload.update(
        {
            "reply_text": "我先帮您记录申请，是否可提前入住需工作人员确认。",
            "intent": "early_check_in",
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
        messages=[{"role": "user", "content": "我想提前入住"}],
    )

    assert decision.handoff_reason == "early_check_in"


@pytest.mark.asyncio
async def test_faq_candidate_is_returned_in_same_structured_guest_response() -> None:
    """知识缺口候选应随主回复返回，且上下文最多含五十个必要字段。"""
    payload = decision_payload()
    payload.update(
        {
            "knowledge_gap": True,
            "knowledge_gap_topic": "停车",
            "faq_candidate": True,
            "faq_candidate_id": 7,
            "faq_canonical_question": "民宿是否提供停车位？",
            "faq_category": "停车",
        }
    )
    client = ChatClientStub([json.dumps(payload, ensure_ascii=False)])
    context = FaqCandidateContextService(CandidateRepositoryStub())
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
        faq_candidate_context=context,
    )

    decision = await assistant.respond(
        guest_identifier="wm-private-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "你们有停车位吗？"}],
    )

    assert decision.faq_candidate is True
    assert decision.faq_candidate_id == 7
    assert decision.faq_canonical_question == "民宿是否提供停车位？"
    assert decision.faq_category == "停车"
    system_prompt = client.chat.completions.requests[0]["messages"][0]["content"]
    assert system_prompt.count('"canonical_question"') == 50
    assert '"id": 50' in system_prompt
    assert '"id": 51' not in system_prompt
    assert "客人原始问法" not in system_prompt
    assert "wm-sensitive" not in system_prompt
    assert "wm-private-guest" not in system_prompt


@pytest.mark.asyncio
async def test_transaction_question_deterministically_clears_faq_candidate() -> None:
    """价格、房态和订单等动态高风险问题不得进入 FAQ 候选。"""
    payload = decision_payload()
    payload.update(
        {
            "knowledge_gap": True,
            "knowledge_gap_topic": "价格",
            "faq_candidate": True,
            "faq_candidate_id": 8,
            "faq_canonical_question": "房间价格是多少？",
            "faq_category": "价格",
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
        messages=[{"role": "user", "content": "今天房间价格是多少？"}],
    )

    assert decision.faq_candidate is False
    assert decision.faq_candidate_id is None
    assert decision.faq_canonical_question is None
    assert decision.faq_category is None


@pytest.mark.asyncio
async def test_non_knowledge_gap_deterministically_clears_faq_candidate() -> None:
    """模型未确认知识缺口时不得保留其候选归类字段。"""
    payload = decision_payload()
    payload.update(
        {
            "faq_candidate": True,
            "faq_candidate_id": 9,
            "faq_canonical_question": "如何协调旅行安排？",
            "faq_category": "旅行",
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

    assert decision.faq_candidate is False
    assert decision.faq_candidate_id is None
    assert decision.faq_canonical_question is None
    assert decision.faq_category is None


@pytest.mark.asyncio
async def test_approved_knowledge_deterministically_clears_faq_candidate() -> None:
    """审核知识已覆盖当前主题时不得生成重复候选。"""
    payload = decision_payload()
    payload.update(
        {
            "knowledge_gap": True,
            "knowledge_gap_topic": "停车",
            "faq_candidate": True,
            "faq_candidate_id": 10,
            "faq_canonical_question": "民宿是否提供停车位？",
            "faq_category": "停车",
        }
    )
    client = ChatClientStub([json.dumps(payload, ensure_ascii=False)])
    assistant = DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=ParkingKnowledgeStub(),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
    )

    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "你们有停车位吗？"}],
    )

    assert decision.faq_candidate is False
    assert decision.faq_candidate_id is None
    assert decision.faq_canonical_question is None
    assert decision.faq_category is None


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
async def test_tourism_reply_skips_second_model_refinement() -> None:
    """已由联网搜索选优并校验的旅游回复不得再次串行调用模型。"""
    client = ChatClientStub([])
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

    assert decision.reply_text.startswith("武汉旅游建议。")
    assert "查询日期：2026-07-30" in decision.reply_text
    assert "参考来源：武汉市文化和旅游局" in decision.reply_text
    assert client.chat.completions.requests == []


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
async def test_tourism_reply_preserves_validated_evidence_without_chat_call() -> None:
    """旅游入口应原样保留搜索层附加的日期和来源证据。"""
    client = ChatClientStub([])
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

    assert "查询日期：2026-07-30" in decision.reply_text
    assert "参考来源：武汉市文化和旅游局" in decision.reply_text
    assert client.chat.completions.requests == []


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
    """首轮空白时第二次请求应丢弃历史，只保留当前问题。"""
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
        messages=[
            {"role": "user", "content": "上一轮问题"},
            {"role": "assistant", "content": "上一轮回答"},
            {"role": "user", "content": "几点入住？"},
        ],
    )

    assert decision.intent == "faq"
    assert len(client.chat.completions.requests) == 2
    first_context = client.chat.completions.requests[0]["messages"][1:]
    retry_context = client.chat.completions.requests[1]["messages"][1:]
    assert [item["content"] for item in first_context] == [
        "上一轮问题",
        "上一轮回答",
        "几点入住？",
    ]
    assert retry_context == [{"role": "user", "content": "几点入住？"}]


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
async def test_room_list_followup_reuses_previous_stay_dates() -> None:
    """“房源列表”应沿用上一轮日期并查询房态，不得要求客人重复说明。"""
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
        messages=[
            {"role": "user", "content": "今天入住明天退房还有房吗？"},
            {
                "role": "assistant",
                "content": "今天入住、明天退房有可用房间。",
            },
            {"role": "user", "content": "房源列表"},
        ],
    )

    assert client.chat.completions.requests[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "search_availability"},
    }
    assert executor.calls[0][0] == "search_availability"


@pytest.mark.asyncio
async def test_new_topic_does_not_reuse_previous_availability_dates() -> None:
    """客人切换到新话题时不得被上一轮房态日期强制查询百居易。"""
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
            {"role": "user", "content": "今天入住明天退房还有房吗？"},
            {
                "role": "assistant",
                "content": "今天入住、明天退房有可用房间。",
            },
            {"role": "user", "content": "怎样和朋友协调旅行安排？"},
        ],
    )

    assert client.chat.completions.requests[0]["tool_choice"] == "auto"


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
async def test_deepseek_context_keeps_only_three_latest_valid_messages() -> None:
    """DeepSeek 结构化对话只携带上一轮问答和当前问题。"""
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
    assert len(context) == 3
    assert [message["content"] for message in context] == [
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
