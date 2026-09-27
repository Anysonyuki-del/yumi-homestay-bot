"""回复分项与精炼的离线回归，禁止无关来源替其他事实背书。"""

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.integrations.deepseek_delivery_rewriter import (
    DeepSeekDeliveryRewriter,
    DeliveryRewriteUnavailableError,
)
from homestay_bot.integrations.tourism import TourismSearchError
from homestay_bot.services.knowledge_evidence_policy import asked_attributes, build_evidence_plan
from homestay_bot.services.knowledge_service import KnowledgeSnippet
from homestay_bot.services.reply_plan import ReplyEvidence
from tests.unit.test_deepseek_client import ChatClientStub, decision_payload


def test_unknown_height_is_not_existence():
    """限高未被理解时不得把收费资料视为完整答案。"""
    assert asked_attributes("停车限高多少？") != {"existence"}


def test_partial_answer_survives_missing_other_topic():
    """停车缺失不清空已经审核的早餐时间。"""
    entry = SimpleNamespace(id=1, answer="早餐每天8:00供应。", scope="global")
    plan = build_evidence_plan(
        "早餐几点？停车怎么收费？",
        [entry],
        is_property_question=True,
        supporting_for_topic=lambda topic, question, entries: (
            entries if topic.name == "早餐" else []
        ),
    )
    assert entry.answer in plan.answers
    assert plan.status == "insufficient"



class Knowledge:
    """保存知识过滤参数，替代外部与数据库查询。"""

    def __init__(self, entries):
        """注入已审核的合成资料。"""
        self.entries = entries
        self.calls = []

    async def retrieve(self, language, question, **kwargs):
        """返回固定资料并记录目标房间与日期。"""
        self.calls.append(kwargs)
        return self.entries


class Search:
    """提供可追溯的合成天气证据或模拟失败。"""

    def __init__(self, *, fails=False):
        """配置明确失败，不调用任何真实网络。"""
        self.fails = fails
        self.calls = []

    async def search(self, **kwargs):
        """把固定查询正文和来源交回助手。"""
        self.calls.append(kwargs)
        if self.fails:
            raise TourismSearchError("degraded")
        reply = "明天武汉阵雨，25～31℃。"
        kwargs["evidence_sink"](
            (
                ReplyEvidence(
                    source_kind="public",
                    source_id="https://weather.invalid/test",
                    target_date=date(2026, 9, 27),
                    fetched_at=datetime.now(UTC),
                    conditions=(reply,),
                ),
            )
        )
        return reply


def make_assistant(knowledge, search=None, client=None, executor=None):
    """使用离线替身构造真实助手链路。"""
    return DeepSeekGuestAssistant(
        chat_client=client or ChatClientStub([json.dumps(decision_payload())]),
        tourism_searcher=search or Search(),
        knowledge=knowledge,
        model="test",
        safety_hmac_key=b"test",
        tool_executor=executor,
        local_date_provider=lambda: date(2026, 9, 26),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_mixed_weather_and_breakfast_keep_independent_results(fails):
    """实时天气成功和失败均不得抹掉早餐原文或冒充人工已接单。"""
    answer = "早餐8:00～9:00供应，需提前一天预约。"
    knowledge = Knowledge([KnowledgeSnippet(1, "早餐", "早餐几点", answer, scope="global")])
    search = Search(fails=fails)
    decision = await make_assistant(knowledge, search).respond(
        guest_identifier="synthetic",
        language=Language.ZH,
        messages=[{"role": "user", "content": "明天天气怎样？早餐几点？"}],
    )
    assert answer in decision.reply_text
    assert decision.task_suggestion is None
    assert decision.handoff_reason is None
    assert len(search.calls) == 1
    assert len(decision.reply_parts) == 2
    public = decision.reply_parts[-1]
    assert public.status == ("query_failed" if fails else "grounded")
    if not fails:
        assert public.evidence[0].source_kind == "public"
        assert public.evidence[0].source_id == "https://weather.invalid/test"
    assert knowledge.calls[0]["target_date"] == date(2026, 9, 27)


def test_property_override_and_cross_date_policies():
    """房间规则覆盖同日同属性通用规则，不同生效日分别说明。"""
    entries = [
        KnowledgeSnippet(1, "早餐", "早餐费用", "早餐免费。", scope="global"),
        KnowledgeSnippet(
            2,
            "早餐",
            "早餐费用",
            "早餐收费20元。",
            scope="property",
            property_id=201,
            valid_until=date(2026, 9, 26),
        ),
        KnowledgeSnippet(
            3,
            "早餐",
            "早餐费用",
            "早餐收费30元。",
            scope="property",
            property_id=201,
            valid_from=date(2026, 9, 27),
        ),
    ]
    plan = build_evidence_plan(
        "早餐多少钱？",
        entries,
        is_property_question=True,
        supporting_for_topic=DeepSeekGuestAssistant._supporting_knowledge,
        target_date=date(2026, 9, 26),
        target_end_date=date(2026, 9, 27),
    )
    assert plan.status == "grounded"
    assert len(plan.parts) == 2
    assert "2026-09-26" in plan.parts[0].text and "20元" in plan.parts[0].text
    assert "2026-09-27" in plan.parts[1].text and "30元" in plan.parts[1].text
    assert all("免费" not in part.text for part in plan.parts)
    assert [part.evidence[0].source_id for part in plan.parts] == ["2", "3"]


def test_conflicting_time_keeps_independent_location():
    """早餐时段相互冲突时仍可回答独立、无冲突的位置资料。"""
    entries = [
        KnowledgeSnippet(index, "早餐", "早餐", answer, scope="global")
        for index, answer in enumerate(
            ["早餐8:00供应。", "早餐9:00供应。", "早餐位于一楼餐厅。"], 1
        )
    ]
    plan = build_evidence_plan(
        "早餐几点？早餐在哪？",
        entries,
        is_property_question=True,
        supporting_for_topic=DeepSeekGuestAssistant._supporting_knowledge,
    )
    assert plan.status == "insufficient"
    assert plan.answers == ("早餐位于一楼餐厅。",)
    assert any(part.status == "missing" for part in plan.parts)


@pytest.mark.asyncio
async def test_refinement_cannot_change_weather_or_remove_conditions():
    """精炼的错误天气被拒绝，安全改写不得删掉已审核条件。"""
    original = "武汉明天阵雨，25～31℃。"
    client = ChatClientStub([json.dumps({"reply_text": "武汉明天晴天，35～40℃。"})])
    assistant = make_assistant(Knowledge([]), client=client)
    assert await assistant._refine_reply(original, force=True) == original
    with pytest.raises(DeliveryRewriteUnavailableError):
        DeepSeekDeliveryRewriter._validate_facts(
            "早餐可以送至房间，仅工作日提供。", "早餐可以送至房间。"
        )


def test_tool_sources_cannot_authorize_other_facts():
    """目录、价格与不可订库存只能产生各自结构化结果，不包含任意服务断言。"""
    parts = DeepSeekGuestAssistant._tool_reply_parts(
        "search_availability",
        {},
        [
            {
                "property_id": 201,
                "property_title": "合成房间",
                "check_in_date": "2026-09-26",
                "check_out_date": "2026-09-27",
                "stay_available": False,
                "remarks": "199元，免费接送",
            }
        ],
        Language.ZH,
        "call-actual",
    )
    assert "不可订" in parts[0].text
    assert "199" not in parts[0].text and "接送" not in parts[0].text
    assert parts[0].evidence[0].source_id == "call-actual:0"
    assert parts[0].evidence[0].property_id == 201
    assert parts[0].evidence[0].target_date == date(2026, 9, 26)
    price = DeepSeekGuestAssistant._tool_reply_parts(
        "search_reference_price",
        {},
        [
            {
                "property_id": 201, "property_title": "合成201",
                "check_in_date": "2026-09-26", "check_out_date": "2026-09-27",
                "nightly_reference_prices": [{"date": "2026-09-26", "price": 199}],
                "inventory": 4,
                "channel_type": "synthetic",
            }
        ],
        Language.ZH,
        "price-call",
    )
    assert "以实际下单为准" in price[0].text
    assert "参考价不代表可订状态" in price[0].text


@pytest.mark.asyncio
async def test_pending_confirmation_survives_weather_branch():
    """有待确认住宿时，天气查询不能吞掉同轮明确确认意图。"""
    import inspect

    from homestay_bot.services.context_retention import CustomerModelContext

    # 只填数据类实际必填字段，避免复制与本用例无关的客户隐私结构。
    fields = inspect.signature(CustomerModelContext).parameters
    values = {
        name: [] for name, field in fields.items() if field.default is inspect.Parameter.empty
    }
    context = CustomerModelContext(**values)
    from dataclasses import replace

    context = replace(context, stay_confirmation={"state": "pending", "order_id": 1})
    payload = decision_payload()
    payload.update(stay_confirmation_intent="confirm", stay_order_id=1)
    client = ChatClientStub([json.dumps(payload)])
    decision = await make_assistant(Knowledge([]), client=client).respond(
        guest_identifier="synthetic",
        language=Language.ZH,
        customer_context=context,
        messages=[{"role": "user", "content": "对，明天天气怎样？"}],
    )
    assert len(client.chat.completions.requests) == 1
    assert decision.stay_confirmation_intent == "confirm"
    assert decision.stay_order_id == 1
    assert "25～31℃" in decision.reply_text
