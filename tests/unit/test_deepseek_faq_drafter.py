import json
from types import SimpleNamespace

import pytest

from homestay_bot.integrations.deepseek_faq_drafter import (
    DeepSeekFaqDrafter,
    FaqDraftUnavailableError,
)


class CompletionsStub:
    """记录 FAQ 草稿请求并返回固定 JSON。"""

    def __init__(self, content: str) -> None:
        """保存模型响应内容。"""
        self.content = content
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        """记录请求并返回 Chat Completions 兼容结构。"""
        self.requests.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ChatClientStub:
    """暴露 FAQ 草稿测试所需的 chat.completions。"""

    def __init__(self, content: str) -> None:
        """初始化固定响应资源。"""
        self.chat = SimpleNamespace(completions=CompletionsStub(content))


def valid_draft_payload() -> dict[str, object]:
    """返回包含待核实占位的完整双语草稿。"""
    return {
        "category": "停车",
        "question_zh": "民宿是否提供停车位？",
        "answer_zh": "停车位数量和收费标准为【待管理员确认】，可先咨询附近停车场。",
        "question_en": "Does the homestay provide parking?",
        "answer_en": (
            "Parking capacity and fees require admin confirmation. "
            "Nearby public parking may be considered."
        ),
        "keywords": ["停车", "停车位"],
        "verification_items": ["停车位数量", "是否收费"],
    }


@pytest.mark.asyncio
async def test_drafter_generates_bilingual_reviewable_faq_without_guest_identity() -> None:
    """草稿必须包含双语问答和待核实项，且请求中没有客人身份。"""
    client = ChatClientStub(
        json.dumps(valid_draft_payload(), ensure_ascii=False)
    )
    drafter = DeepSeekFaqDrafter(
        client=client,
        model="deepseek-v4-flash",
    )

    draft = await drafter.generate(
        canonical_question="民宿是否提供停车位？",
        category="停车",
        examples=["民宿有停车位吗？", "开车过去停车方便吗？"],
        approved_knowledge=[
            {
                "category": "交通",
                "question_zh": "怎么到民宿？",
                "answer_zh": "请按导航前往。",
                "question_en": "How can I get there?",
                "answer_en": "Please follow the map.",
            }
        ],
    )

    request = client.chat.completions.requests[0]
    serialized = json.dumps(request, ensure_ascii=False)
    assert draft.question_zh == "民宿是否提供停车位？"
    assert draft.verification_items == ["停车位数量", "是否收费"]
    assert "【待管理员确认】" in draft.answer_zh
    assert "不得参考其他民宿" in request["messages"][0]["content"]
    assert "民宿有停车位吗" in request["messages"][1]["content"]
    assert "external_userid" not in serialized
    assert "guest" not in serialized.lower()
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_drafter_rejects_unverified_facts_without_placeholder() -> None:
    """有待核实事项时，中文答案缺少明确占位必须拒绝。"""
    payload = valid_draft_payload()
    payload["answer_zh"] = "我们提供充足的免费停车位。"
    drafter = DeepSeekFaqDrafter(
        client=ChatClientStub(json.dumps(payload, ensure_ascii=False)),
        model="deepseek-v4-flash",
    )

    with pytest.raises(FaqDraftUnavailableError):
        await drafter.generate(
            canonical_question="民宿是否提供停车位？",
            category="停车",
            examples=["有停车位吗？"],
            approved_knowledge=[],
        )


@pytest.mark.asyncio
async def test_drafter_rejects_links_in_draft() -> None:
    """FAQ 草稿不得夹带网址或 Markdown 链接。"""
    payload = valid_draft_payload()
    payload["answer_zh"] = "请查看 https://example.com，【待管理员确认】。"
    drafter = DeepSeekFaqDrafter(
        client=ChatClientStub(json.dumps(payload, ensure_ascii=False)),
        model="deepseek-v4-flash",
    )

    with pytest.raises(FaqDraftUnavailableError):
        await drafter.generate(
            canonical_question="民宿是否提供停车位？",
            category="停车",
            examples=["有停车位吗？"],
            approved_knowledge=[],
        )


@pytest.mark.asyncio
async def test_drafter_wraps_invalid_json_as_stable_error() -> None:
    """模型返回无效 JSON 时只暴露稳定领域异常。"""
    drafter = DeepSeekFaqDrafter(
        client=ChatClientStub("not-json"),
        model="deepseek-v4-flash",
    )

    with pytest.raises(FaqDraftUnavailableError):
        await drafter.generate(
            canonical_question="民宿是否提供停车位？",
            category="停车",
            examples=["有停车位吗？"],
            approved_knowledge=[],
        )


@pytest.mark.asyncio
async def test_drafter_rejects_ungrounded_property_fact_when_model_omits_checks() -> None:
    """专属停车无相关审核知识时，模型清空待核实项也不得绕过占位要求。"""
    payload = valid_draft_payload()
    payload["answer_zh"] = "民宿提供两个免费停车位。"
    payload["verification_items"] = []
    drafter = DeepSeekFaqDrafter(
        client=ChatClientStub(json.dumps(payload, ensure_ascii=False)),
        model="deepseek-v4-flash",
    )

    with pytest.raises(FaqDraftUnavailableError):
        await drafter.generate(
            canonical_question="民宿是否提供停车位？",
            category="停车",
            examples=["有停车位吗？"],
            approved_knowledge=[
                {
                    "category": "交通",
                    "question_zh": "怎么到民宿？",
                    "answer_zh": "请按导航前往。",
                    "question_en": "How can I get there?",
                    "answer_en": "Please follow navigation.",
                }
            ],
        )


@pytest.mark.asyncio
async def test_drafter_accepts_property_fact_confirmed_by_relevant_knowledge() -> None:
    """相关审核知识明确确认停车事实时，草稿无需强制待确认占位。"""
    payload = valid_draft_payload()
    payload["answer_zh"] = "民宿提供两个免费停车位。"
    payload["verification_items"] = []
    drafter = DeepSeekFaqDrafter(
        client=ChatClientStub(json.dumps(payload, ensure_ascii=False)),
        model="deepseek-v4-flash",
    )

    draft = await drafter.generate(
        canonical_question="民宿是否提供停车位？",
        category="停车",
        examples=["有停车位吗？"],
        approved_knowledge=[
            {
                "category": "停车",
                "question_zh": "民宿有几个停车位？",
                "answer_zh": "民宿提供两个免费停车位。",
                "question_en": "How many parking spaces are available?",
                "answer_en": "Two free parking spaces are available.",
            }
        ],
    )

    assert draft.answer_zh == "民宿提供两个免费停车位。"
    assert draft.verification_items == []
