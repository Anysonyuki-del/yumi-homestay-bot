import json
from types import SimpleNamespace

import pytest

from homestay_bot.integrations.deepseek_complaint import (
    ComplaintDraftUnavailableError,
    DeepSeekComplaintAnalyzer,
)


class CompletionsStub:
    """返回固定客诉 JSON 并记录模型请求。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        """模拟 Chat Completions。"""
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class ClientStub:
    """提供客诉分析所需的 Chat Completions 接口。"""

    def __init__(self, content: str) -> None:
        self.chat = SimpleNamespace(completions=CompletionsStub(content))


def valid_payload() -> dict[str, object]:
    """返回不包含金额承诺的安全客诉分析。"""
    return {
        "core_issue": "客人等待入住时间较长",
        "customer_request": "希望解释延误原因并核实补偿可能性",
        "emotion_level": "upset",
        "customer_claims": ["15:00尚未完成入住"],
        "known_facts": ["订单约定入住时间为15:00"],
        "facts_to_verify": ["实际完成交付时间"],
        "responsibility_risk": "待核实",
        "refund_or_compensation": True,
        "platform_escalation_risk": False,
        "reply_tone": "先承认不便，再说明正在核实",
        "reply_draft": (
            "很抱歉让您久等了，我已记录情况并正在核实，"
            "退款或补偿方案会由管家确认后回复您。"
        ),
    }


@pytest.mark.asyncio
async def test_analyzer_returns_structured_complaint_draft_without_identity() -> None:
    """分析结果包含事实分层和草稿，但请求不携带客人身份。"""
    client = ClientStub(json.dumps(valid_payload(), ensure_ascii=False))
    analyzer = DeepSeekComplaintAnalyzer(client=client, model="deepseek-v4-flash")

    result = await analyzer.generate(
        reason="refund",
        risk_level="high",
        messages=[{"role": "user", "content": "15点还不能入住，我要投诉"}],
        customer_context={"active_orders": [{"check_in_date": "2026-08-01"}]},
    )

    assert result.refund_or_compensation is True
    assert "管家确认" in result.reply_draft
    request = client.chat.completions.requests[0]
    serialized = json.dumps(request, ensure_ascii=False)
    assert "external_userid" not in serialized
    assert "手机号" in request["messages"][0]["content"]
    assert request["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_analyzer_rejects_refund_amount_or_responsibility_commitment() -> None:
    """模型不得替人工承诺责任、退款或赔偿金额。"""
    payload = valid_payload()
    payload["reply_draft"] = "民宿肯定有责任，马上赔偿500元并退款。"
    analyzer = DeepSeekComplaintAnalyzer(
        client=ClientStub(json.dumps(payload, ensure_ascii=False)),
        model="deepseek-v4-flash",
    )

    with pytest.raises(ComplaintDraftUnavailableError):
        await analyzer.generate(
            reason="refund",
            risk_level="high",
            messages=[{"role": "user", "content": "我要退款"}],
            customer_context={},
        )


@pytest.mark.asyncio
async def test_analyzer_wraps_invalid_json_as_stable_error() -> None:
    """非法 JSON 只转换为稳定领域异常。"""
    analyzer = DeepSeekComplaintAnalyzer(
        client=ClientStub("not-json"),
        model="deepseek-v4-flash",
    )

    with pytest.raises(ComplaintDraftUnavailableError):
        await analyzer.generate(
            reason="complaint",
            risk_level="high",
            messages=[{"role": "user", "content": "我要投诉"}],
            customer_context={},
        )
