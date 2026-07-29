import json
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.hostex_client import (
    AvailabilityDay,
    Property,
    PropertyAvailability,
    Reservation,
)
from homestay_bot.integrations.openai_client import (
    GuestAssistant,
    HostexReadOnlyToolExecutor,
)
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


class HostexStub:
    """模拟百居易只读接口，并记录订单查询参数。"""

    def __init__(self) -> None:
        self.reservation_query = None

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

    async def list_reservations(self, query) -> list[Reservation]:
        """记录查询条件并返回订单摘要。"""
        self.reservation_query = query
        return [
            Reservation(
                reservation_code="R-1",
                stay_code="S-1",
                property_id=101,
                check_in_date="2026-08-01",
                check_out_date="2026-08-02",
                status="accepted",
                created_at="2026-07-29T00:00:00+08:00",
            )
        ]


def test_openai_tools_do_not_include_create_reservation() -> None:
    """模型可查房态，但永远不能直接获得创建订单工具。"""
    assistant = GuestAssistant(
        client=OpenAIStub(),
        knowledge=KnowledgeStub(),
        model="gpt-5.6-terra",
        safety_hmac_key=b"test-key",
    )

    tool_names = {tool["name"] for tool in assistant.tool_definitions()}

    assert "search_availability" in tool_names
    assert "search_reference_price" in tool_names
    assert "lookup_reservation" in tool_names
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
    assert second_request["previous_response_id"] == "resp-1"
    assert second_request["input"] == [
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
    reservations = await executor.execute(
        "lookup_reservation",
        {"reservation_code": "R-1"},
    )

    assert availability[0]["property_id"] == 101
    assert availability[0]["days"][0]["date"] == "2026-08-01"
    assert reservations[0]["reservation_code"] == "R-1"
    assert hostex.reservation_query.reservation_code == "R-1"


@pytest.mark.asyncio
async def test_hostex_tool_executor_rejects_unknown_or_write_tool() -> None:
    """执行器必须拒绝白名单外名称，尤其不能绕过审批创建订单。"""
    executor = HostexReadOnlyToolExecutor(HostexStub())

    with pytest.raises(ValueError, match="不允许"):
        await executor.execute("create_reservation", {})
