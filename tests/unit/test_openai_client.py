import json
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.hostex_client import AvailabilityDay, Property, PropertyAvailability
from homestay_bot.integrations.openai_client import (
    GuestAssistant,
    HostexReadOnlyToolExecutor,
)
from homestay_bot.integrations.tourism import TourismSearchError
from homestay_bot.services.knowledge_service import KnowledgeSnippet


class KnowledgeStub:
    """返回固定的已审核知识上下文。"""

    async def build_context(self, language: Language) -> list[KnowledgeSnippet]:
        """提供与测试语言一致的知识。"""
        return [
            KnowledgeSnippet(
                source_id=1,
                category="入住",
                question="几点入住？",
                answer="下午三点后入住。",
            )
        ]


class ResponsesStub:
    """捕获 Responses API 请求并返回结构化决定。"""

    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def create(self, **kwargs):
        """返回无需工具调用的客服决定。"""
        self.kwargs = kwargs
        payload = {
            "reply_text": "下午三点后可以入住。",
            "language": "zh",
            "intent": "faq",
            "confidence": 0.98,
            "handoff_reason": None,
            "booking_fields": None,
        }
        return SimpleNamespace(output=[], output_text=json.dumps(payload))


class OpenAIStub:
    """模拟 OpenAI SDK 的 responses 资源。"""

    def __init__(self) -> None:
        self.responses = ResponsesStub()


class LowConfidenceResponsesStub:
    """模拟模型无法从现有资料确定答案的情况。"""

    async def create(self, **kwargs):
        """故意漏填接管原因，验证应用层安全兜底。"""
        payload = {
            "reply_text": "这个问题我暂时无法确认，正在为您联系工作人员。",
            "language": "zh",
            "intent": "unknown",
            "confidence": 0.35,
            "handoff_reason": None,
            "booking_fields": None,
        }
        return SimpleNamespace(output=[], output_text=json.dumps(payload))


class LowConfidenceOpenAIStub:
    """暴露低置信度 Responses 模拟资源。"""

    def __init__(self) -> None:
        self.responses = LowConfidenceResponsesStub()


class ToolExecutorStub:
    """捕获模型提出的只读工具调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def execute(self, name: str, arguments: dict[str, str]) -> dict[str, object]:
        """返回固定房态数据，便于验证调用闭环。"""
        self.calls.append((name, arguments))
        return {"available": True, "rooms": 1}


class ToolCallingResponsesStub:
    """先请求只读工具，再返回最终结构化回复。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        """根据调用轮次模拟 Responses API 的工具闭环。"""
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            call = SimpleNamespace(
                type="function_call",
                name="search_availability",
                arguments=json.dumps(
                    {
                        "check_in_date": "2026-08-01",
                        "check_out_date": "2026-08-02",
                    }
                ),
                call_id="call-1",
            )
            return SimpleNamespace(id="resp-1", output=[call], output_text="")

        payload = {
            "reply_text": "该日期还有一间可订。",
            "language": "zh",
            "intent": "availability",
            "confidence": 0.95,
            "handoff_reason": None,
            "booking_fields": None,
        }
        return SimpleNamespace(id="resp-2", output=[], output_text=json.dumps(payload))


class ToolCallingOpenAIStub:
    """模拟会发起一次工具调用的 OpenAI 客户端。"""

    def __init__(self) -> None:
        self.responses = ToolCallingResponsesStub()


class TourismResponsesStub:
    """返回带官方来源注解的旅游结构化结果。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        """记录唯一请求并返回旅游推荐和来源。"""
        self.requests.append(kwargs)
        payload = {
            "reply_text": "推荐黄鹤楼、东湖和湖北省博物馆。",
            "language": "zh",
            "intent": "tourism",
            "confidence": 0.95,
            "handoff_reason": None,
            "booking_fields": None,
        }
        citation = SimpleNamespace(
            type="url_citation",
            url="https://wlj.wuhan.gov.cn/",
            title="武汉市文化和旅游局",
        )
        message = SimpleNamespace(
            type="message",
            content=[SimpleNamespace(annotations=[citation])],
        )
        return SimpleNamespace(output=[message], output_text=json.dumps(payload))


class TourismOpenAIStub:
    """暴露带引用的旅游 Responses 模拟资源。"""

    def __init__(self) -> None:
        self.responses = TourismResponsesStub()


class UnsupportedWebSearchError(RuntimeError):
    """模拟兼容端点明确拒绝 web_search 工具。"""

    status_code = 400


class FailingResponsesStub:
    """模拟 Fenno 明确不支持联网工具。"""

    async def create(self, **kwargs):
        """抛出包含工具名称的 400 错误。"""
        raise UnsupportedWebSearchError("web_search unsupported")


class MissingCitationResponsesStub(TourismResponsesStub):
    """模拟模型给出正文但没有可验证来源。"""

    async def create(self, **kwargs):
        """返回没有 url_citation 的结构化结果。"""
        response = await super().create(**kwargs)
        response.output[0].content[0].annotations = []
        return response


class HostexStub:
    """模拟百居易房态与参考价只读接口。"""

    async def list_properties(self) -> list[Property]:
        """返回一间物理房间。"""
        return [Property(id=101, title="江景大床房")]

    async def list_availabilities(
        self, property_ids, start_date, end_date
    ) -> list[PropertyAvailability]:
        """返回该房间的可用房态。"""
        return [
            PropertyAvailability(
                property_id=property_ids[0],
                days=[
                    AvailabilityDay(
                        date=start_date,
                        available=True,
                    )
                ],
            )
        ]

    async def list_reference_prices(self, start_date, end_date) -> list:
        """本测试不需要参考价明细。"""
        return []


def test_openai_tools_expose_only_non_personal_read_queries() -> None:
    """客人模型只能查房态和参考价，不能查询订单或创建订单。"""
    assistant = GuestAssistant(
        client=OpenAIStub(),
        knowledge=KnowledgeStub(),
        model="gpt-5.6-terra",
        safety_hmac_key=b"test-key",
    )

    tool_names = {tool["name"] for tool in assistant.tool_definitions()}

    assert "search_availability" in tool_names
    assert "search_reference_price" in tool_names
    assert "lookup_reservation" not in tool_names
    assert "create_reservation" not in tool_names


@pytest.mark.asyncio
async def test_response_disables_storage_and_hashes_guest_identifier() -> None:
    """OpenAI 请求必须关闭应用状态存储并避免发送企业微信原始 ID。"""
    client = OpenAIStub()
    assistant = GuestAssistant(
        client=client,
        knowledge=KnowledgeStub(),
        model="gpt-5.6-terra",
        safety_hmac_key=b"test-key",
    )

    decision = await assistant.respond(
        guest_identifier="wm-sensitive-id",
        language=Language.ZH,
        messages=[{"role": "user", "content": "几点入住？"}],
    )

    assert decision.reply_text == "下午三点后可以入住。"
    assert client.responses.kwargs["store"] is False
    assert client.responses.kwargs["safety_identifier"] != "wm-sensitive-id"
    assert "wm-sensitive-id" not in json.dumps(
        client.responses.kwargs, ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_response_uses_flat_strict_schema_for_compatible_endpoints() -> None:
    """结构化输出 Schema 不应包含兼容接口无法解析的引用或缺失必填字段。"""
    client = OpenAIStub()
    assistant = GuestAssistant(
        client=client,
        knowledge=KnowledgeStub(),
        model="gpt-5.4-mini",
        safety_hmac_key=b"test-key",
    )

    await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "当前房间预订状况"}],
    )

    schema = client.responses.kwargs["text"]["format"]["schema"]
    assert "$defs" not in schema
    assert set(schema["required"]) == set(schema["properties"])
    booking_schema = schema["properties"]["booking_fields"]["anyOf"][0]
    assert set(booking_schema["required"]) == set(booking_schema["properties"])


@pytest.mark.asyncio
async def test_missing_dates_are_clarified_without_human_handoff() -> None:
    """普通房态咨询缺少日期时应继续追问，不应仅因资料不全转人工。"""
    client = OpenAIStub()
    assistant = GuestAssistant(
        client=client,
        knowledge=KnowledgeStub(),
        model="gpt-5.4-mini",
        safety_hmac_key=b"test-key",
    )

    await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "当前房间预订状况"}],
    )

    instructions = str(client.responses.kwargs["instructions"])
    assert "缺少入住或退房日期时应直接追问，不得仅因此转人工" in instructions


@pytest.mark.asyncio
async def test_response_executes_read_only_tool_and_returns_final_decision() -> None:
    """模型的只读函数调用应执行并把结果回传后再生成回复。"""
    client = ToolCallingOpenAIStub()
    executor = ToolExecutorStub()
    assistant = GuestAssistant(
        client=client,
        knowledge=KnowledgeStub(),
        model="gpt-5.6-terra",
        safety_hmac_key=b"test-key",
        tool_executor=executor,
    )

    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "8月1日还有房吗？"}],
    )

    assert decision.reply_text == "该日期还有一间可订。"
    assert executor.calls == [
        (
            "search_availability",
            {
                "check_in_date": "2026-08-01",
                "check_out_date": "2026-08-02",
            },
        )
    ]
    second_request = client.responses.requests[1]
    assert second_request["input"] == [
        {
            "type": "function_call",
            "name": "search_availability",
            "arguments": (
                '{"check_in_date": "2026-08-01", '
                '"check_out_date": "2026-08-02"}'
            ),
            "call_id": "call-1",
        },
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": '{"available": true, "rooms": 1}',
        }
    ]


@pytest.mark.asyncio
async def test_low_confidence_response_forces_human_handoff() -> None:
    """即使模型漏填原因，低置信度结果也必须进入人工接管。"""
    assistant = GuestAssistant(
        client=LowConfidenceOpenAIStub(),
        knowledge=KnowledgeStub(),
        model="gpt-5.6-terra",
        safety_hmac_key=b"test-key",
    )

    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "能不能帮我处理一个特殊问题？"}],
    )

    assert decision.handoff_reason == "low_confidence"


@pytest.mark.asyncio
async def test_non_booking_context_redacts_explicit_name_and_mobile() -> None:
    """普通问答不应把客人明确写出的姓名和手机号发送给模型。"""
    client = OpenAIStub()
    assistant = GuestAssistant(
        client=client,
        knowledge=KnowledgeStub(),
        model="gpt-5.6-terra",
        safety_hmac_key=b"test-key",
    )

    await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[
            {
                "role": "user",
                "content": "我叫张三，手机号13800138000，请问几点退房？",
            }
        ],
    )

    request_text = json.dumps(client.responses.kwargs, ensure_ascii=False)
    assert "13800138000" not in request_text
    assert "张三" not in request_text


@pytest.mark.asyncio
async def test_hostex_tool_executor_exposes_only_serializable_read_results() -> None:
    """百居易工具执行器应把只读模型转换为可安全回传的普通数据。"""
    hostex = HostexStub()
    executor = HostexReadOnlyToolExecutor(hostex)

    availability = await executor.execute(
        "search_availability",
        {
            "check_in_date": "2026-08-01",
            "check_out_date": "2026-08-02",
        },
    )
    assert availability[0]["property_id"] == 101
    assert availability[0]["days"][0]["date"] == "2026-08-01"


@pytest.mark.asyncio
async def test_hostex_tool_executor_rejects_unknown_or_write_tool() -> None:
    """执行器必须拒绝白名单外名称，尤其不能绕过审批创建订单。"""
    executor = HostexReadOnlyToolExecutor(HostexStub())

    with pytest.raises(ValueError, match="不允许"):
        await executor.execute("create_reservation", {})


@pytest.mark.asyncio
async def test_tourism_query_uses_one_required_web_search_request() -> None:
    """旅游问题应只发起一次联网请求，并固定武汉位置和来源明细。"""
    client = TourismOpenAIStub()
    statuses: list[str] = []
    assistant = GuestAssistant(
        client=client,
        knowledge=KnowledgeStub(),
        model="gpt-5.4-mini",
        safety_hmac_key=b"test-key",
        web_search_status_setter=statuses.append,
    )

    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "武汉有哪些地方好玩？"}],
    )

    assert len(client.responses.requests) == 1
    request = client.responses.requests[0]
    assert request["tools"] == [
        {
            "type": "web_search",
            "search_context_size": "low",
            "user_location": {
                "type": "approximate",
                "country": "CN",
                "city": "Wuhan",
                "region": "Hubei",
            },
        }
    ]
    assert request["tool_choice"] == {"type": "web_search"}
    assert request["include"] == ["web_search_call.action.sources"]
    assert "武汉市文化和旅游局" in decision.reply_text
    assert "参考来源：武汉市文化和旅游局" in decision.reply_text
    assert "http://" not in decision.reply_text
    assert "https://" not in decision.reply_text
    assert decision.handoff_reason is None
    assert statuses == ["ok"]


@pytest.mark.asyncio
async def test_tourism_search_sends_only_redacted_latest_question() -> None:
    """联网请求不得携带历史姓名、手机号或企业微信 ID。"""
    client = TourismOpenAIStub()
    assistant = GuestAssistant(
        client=client,
        knowledge=KnowledgeStub(),
        model="gpt-5.4-mini",
        safety_hmac_key=b"test-key",
    )

    await assistant.respond(
        guest_identifier="wm-sensitive-id",
        language=Language.ZH,
        messages=[
            {"role": "user", "content": "我叫张三，手机号13800138000"},
            {"role": "assistant", "content": "您好"},
            {"role": "user", "content": "武汉最近有什么展览？"},
        ],
    )

    request_text = json.dumps(client.responses.requests[0], ensure_ascii=False)
    assert "张三" not in request_text
    assert "13800138000" not in request_text
    assert "wm-sensitive-id" not in request_text
    assert client.responses.requests[0]["input"] == [
        {"role": "user", "content": "武汉最近有什么展览？"}
    ]


@pytest.mark.asyncio
async def test_booking_query_keeps_hostex_tools_without_web_search() -> None:
    """房态问题不得获得 web_search 工具。"""
    client = OpenAIStub()
    assistant = GuestAssistant(
        client=client,
        knowledge=KnowledgeStub(),
        model="gpt-5.4-mini",
        safety_hmac_key=b"test-key",
    )

    await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "8月1日还有房吗？"}],
    )

    assert all(
        tool["type"] == "function" for tool in client.responses.kwargs["tools"]
    )
    assert "tool_choice" not in client.responses.kwargs
    assert "include" not in client.responses.kwargs


@pytest.mark.asyncio
async def test_unsupported_web_search_is_classified_for_handoff() -> None:
    """兼容端点明确拒绝工具时应归类为 unsupported。"""
    statuses: list[str] = []
    client = SimpleNamespace(responses=FailingResponsesStub())
    assistant = GuestAssistant(
        client=client,
        knowledge=KnowledgeStub(),
        model="gpt-5.4-mini",
        safety_hmac_key=b"test-key",
        web_search_status_setter=statuses.append,
    )

    with pytest.raises(TourismSearchError) as caught:
        await assistant.respond(
            guest_identifier="wm-guest",
            language=Language.ZH,
            messages=[{"role": "user", "content": "武汉有哪些地方好玩？"}],
        )

    assert caught.value.status == "unsupported"
    assert statuses == ["unsupported"]


@pytest.mark.asyncio
async def test_tourism_answer_without_citations_is_degraded() -> None:
    """没有 URL 引用的旅游正文不得作为实时答案发送。"""
    statuses: list[str] = []
    client = SimpleNamespace(responses=MissingCitationResponsesStub())
    assistant = GuestAssistant(
        client=client,
        knowledge=KnowledgeStub(),
        model="gpt-5.4-mini",
        safety_hmac_key=b"test-key",
        web_search_status_setter=statuses.append,
    )

    with pytest.raises(TourismSearchError) as caught:
        await assistant.respond(
            guest_identifier="wm-guest",
            language=Language.ZH,
            messages=[{"role": "user", "content": "武汉有哪些地方好玩？"}],
        )

    assert caught.value.status == "degraded"
    assert statuses == ["degraded"]
