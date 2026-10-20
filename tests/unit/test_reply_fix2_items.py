"""第二轮修复阶段一：逐项候选、按对象约束、小句覆盖与客诉合并（Spec 2026-10-08 fix2 §2.1–§2.3）。

断言最终客人正文与分项来源，不只断言函数返回；改动前这些用例应失败。
"""

import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import (
    _STABLE_RECOMMENDATION_RULE,
    AssistantRequestContext,
    DeepSeekGuestAssistant,
)
from homestay_bot.services.complaint_service import ComplaintService
from homestay_bot.services.knowledge_evidence_policy import (
    build_evidence_plan,
    object_question_key,
    same_object,
    verify_selected_evidence,
)
from homestay_bot.services.knowledge_service import (
    KnowledgeSnippet,
    _passes_triggers,
    normalize_text,
)
from homestay_bot.services.reply_plan import ReplyPart
from homestay_bot.services.turn_plan import failed_plan
from tests.plan_helpers import PLAN_TODAY, plan_for

UNCONFIRMED = "尚未确认"


def _snippet(source_id: int, question: str, answer: str, **fields: Any) -> KnowledgeSnippet:
    """构造一条审核知识。"""
    return KnowledgeSnippet(
        source_id=source_id,
        category=fields.pop("category", "设施"),
        question=question,
        answer=answer,
        scope=fields.pop("scope", "global"),
        **fields,
    )


class _Knowledge:
    """按真实检索的房源口径过滤：房间专属条目只对同一房源可见；记录每次检索参数。"""

    def __init__(self, entries: list[KnowledgeSnippet], rooms: dict[str, int] | None = None):
        """保存条目与房号映射。"""
        self.entries = entries
        self.rooms = rooms or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def retrieve(self, language, query, **kwargs):
        """返回该房源可见的全部条目。"""
        self.calls.append((query, kwargs))
        property_id = kwargs.get("property_id")
        return [
            entry
            for entry in self.entries
            if entry.scope != "property" or entry.property_id == property_id
        ]

    async def room_profiles(self) -> list[tuple[int, str | None, str]]:
        """按房号映射表给出房源资料（room_number 即映射键），解析规则与生产相同。"""
        return [(property_id, room, "") for room, property_id in self.rooms.items()]


def _decision(**fields: Any) -> str:
    """主调用返回的最小合法决定 JSON。"""
    return json.dumps(
        {"reply_text": "好的。", "language": "zh", "intent": "faq", "confidence": 0.9, **fields},
        ensure_ascii=False,
    )


def _tool_message(name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    """主调用请求一次只读工具。"""
    raw = json.dumps(arguments)
    call = SimpleNamespace(
        id=f"call-{name}", type="function",
        function=SimpleNamespace(name=name, arguments=raw),
    )
    return SimpleNamespace(
        content=None,
        tool_calls=[call],
        model_dump=lambda **_: {
            "role": "assistant",
            "tool_calls": [{"id": call.id, "type": "function",
                            "function": {"name": name, "arguments": raw}}],
        },
    )


class _Scripted:
    """按顺序返回预置响应：字符串为决定正文，SimpleNamespace 为工具调用消息。"""

    def __init__(self, responses: list[Any]) -> None:
        """保存响应序列并记录请求。"""
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        """返回下一条响应；超出时重复最后一条。"""
        self.requests.append(kwargs)
        item = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        message = item if isinstance(item, SimpleNamespace) else SimpleNamespace(
            content=item, tool_calls=None
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _Tools:
    """只读工具替身：按工具名返回预置行。"""

    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        """保存各工具的返回行。"""
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, name: str, arguments: dict[str, Any]):
        """记录调用并返回预置行。"""
        self.calls.append((name, arguments))
        return self.rows.get(name, [])


class _Tourism:
    """联网搜索替身：按问题返回固定正文。"""

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        """保存每组问题的答案。"""
        self.answers = answers or {}
        self.questions: list[str] = []

    async def search(self, *, question, **kwargs):
        """返回该组答案。"""
        self.questions.append(question)
        return self.answers.get(question, "今天武汉晴，22至28摄氏度。")


def _assistant(responses, knowledge=None, tools=None, tourism=None):
    """构造适配器；计划由用例直接传入，不调用规划模型。"""
    completions = _Scripted(responses)
    assistant = DeepSeekGuestAssistant(
        chat_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        tourism_searcher=tourism or _Tourism(),
        knowledge=knowledge or _Knowledge([]),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
        tool_executor=tools,
        local_date_provider=lambda: PLAN_TODAY,
        plan_turns=False,
    )
    return assistant, completions


async def _respond(assistant, text: str, plan, **kwargs):
    """以给定计划回复一句话。"""
    return await assistant.respond(
        guest_identifier="g", language=Language.ZH,
        messages=[{"role": "user", "content": text}], turn_plan=plan, **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("day,expected", [(13, "scope_unbound"), (11, "knowledge_missing")])
async def test_static_clause_uses_its_own_day(fallback: bool, day: int, expected: str) -> None:
    """静态小句只核所问当天；另一天的早餐不得混进正常或协议回退回复。"""
    text = f"10月10日401住2晚有房吗，10月{day}日早餐几点开始"
    plan = plan_for(text, ("stay_query", text, {
        "target_room": "401", "check_in_date": "2026-10-10", "check_out_date": "2026-10-12",
    }))
    breakfast = _snippet(9601, "早餐几点开始？", "早餐7:30开始。", category="早餐",
                         valid_from=date(2026, 10, 10), valid_until=date(2026, 10, 10))
    args = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-12"}
    tools = _Tools({"search_availability": [_availability_row(1401, *args.values())]})
    assistant, _ = _assistant(
        [_tool_message("search_availability", args), "invalid" if fallback else _decision()],
        _Knowledge([breakfast], {"401": 1401}), tools,
    )
    decision = await _respond(assistant, text, plan)
    assert "7:30" not in decision.reply_text
    assert any(unit.reason == expected and "早餐" in unit.text for unit in decision.gap_units)
    assert decision.referral_candidates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("separator", ["和", "，"])
async def test_tool_price_words_do_not_change_breakfast_attributes(fallback, separator) -> None:
    """房价锚点与早餐时间同句时，只用剩余语义取证；ISO 年份也不算房号。"""
    text = f"2026-10-10 401房间住一晚多少钱{separator}早餐几点开始"
    plan = plan_for(text, ("stay_query", text, {
        "target_room": "401", "check_in_date": "2026-10-10", "check_out_date": "2026-10-11",
    }))
    args = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-11"}
    tools = _Tools({"search_availability": [_availability_row(1401, *args.values())]})
    assistant, _ = _assistant(
        [_tool_message("search_availability", args), "invalid" if fallback else _decision()],
        _Knowledge([_snippet(9602, "早餐几点开始？", "早餐8:30开始。", category="早餐")],
                   {"401": 1401}), tools,
    )
    decision = await _respond(assistant, text, plan)
    assert "8:30" in decision.reply_text
    assert all(unit.reason != "scope_unbound" for unit in decision.gap_units)


@pytest.mark.asyncio
async def test_each_static_item_retrieves_its_own_absolute_day() -> None:
    """计划未给日期时，自身摘录日期决定检索，不受整轮第一日影响。"""
    first, second = "本店10月10日早餐几点开始", "本店10月11日早餐几点开始"
    text = first + "，" + second
    knowledge = _Knowledge([
        _snippet(9801, "早餐几点开始？", "早餐7:30开始。", category="早餐",
                 valid_from=date(2026, 10, 10), valid_until=date(2026, 10, 10)),
        _snippet(9802, "早餐几点开始？", "早餐8:30开始。", category="早餐",
                 valid_from=date(2026, 10, 11), valid_until=date(2026, 10, 11)),
    ])
    assistant, _ = _assistant([_decision()], knowledge)
    plan = plan_for(text, ("static_fact", first), ("static_fact", second))
    decision = await _respond(assistant, text, plan)
    dates = [kwargs["target_date"] for _, kwargs in knowledge.calls if kwargs.get("trigger_text")]
    assert dates == [date(2026, 10, 10), date(2026, 10, 11)]
    assert "7:30" in decision.reply_text and "8:30" in decision.reply_text
    assert decision.gap_units == []


CHECKOUT_401 = _snippet(
    7001, "几点退房？", "401房间退房时间为12:00。", scope="property", property_id=1401,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["static_fact", "catalog_query"])
@pytest.mark.parametrize("selection", [None, [], [{"item_id": 1, "answer_ids": [7001]}]])
async def test_unknown_room_never_borrows_the_confirmed_stay_policy(kind, selection) -> None:
    """点名的房号映射不到时，不管模型是否回传选择，都不发 401 的退房政策（F1、G1）。"""
    text = "909房间几点退房"
    plan = plan_for(text, (kind, text, {"target_room": "909"}))
    fields = {"evidence_selection": selection} if selection is not None else {}
    assistant, _ = _assistant([_decision(reply_text="12:00退房。", **fields)],
                              _Knowledge([CHECKOUT_401], {"401": 1401}))
    decision = await _respond(
        assistant, text, plan, request_context=AssistantRequestContext(property_id=1401)
    )
    assert "12:00" not in decision.reply_text
    assert all(
        evidence.property_id != 1401
        for part in decision.reply_parts
        for evidence in part.evidence
    )


def test_trigger_conditions_split_between_quote_and_full_text() -> None:
    """正向条件只看本项摘录，排除条件看完整原文（F2、G2，D-G2、D-G4）。"""
    night = SimpleNamespace(trigger_any=["晚上"], trigger_exclude=[])
    assert not _passes_triggers(
        night, normalize_text("白天停车多少钱"),
        normalize_text("白天停车多少钱，晚上几点以后要保持安静"),
    )
    electric = SimpleNamespace(trigger_any=[], trigger_exclude=["电动车"])
    assert not _passes_triggers(
        electric, normalize_text("停车多少钱"), normalize_text("我的车是电动车，请问停车多少钱"),
    )
    plain = SimpleNamespace(trigger_any=[], trigger_exclude=[])
    assert _passes_triggers(plain, normalize_text("停车多少钱"))


@pytest.mark.asyncio
async def test_item_retrieval_passes_quote_and_full_text_and_ignores_whole_turn_pool() -> None:
    """逐项检索传本项摘录与完整原文；整句检索结果不进入该项合法候选（F2）。"""
    text = "白天停车多少钱，晚上几点以后要保持安静"
    plan = plan_for(
        text, ("static_fact", "白天停车多少钱"), ("static_fact", "晚上几点以后要保持安静")
    )

    class _ByQuery(_Knowledge):
        async def retrieve(self, language, query, **kwargs):
            self.calls.append((query, kwargs))
            # 停车条目只在整句检索时出现，逐项检索（带 trigger_text）检不到它。
            return [] if kwargs.get("trigger_text") else list(self.entries)

    parking = _snippet(8001, "停车费多少？", "晚上停车20元。")
    knowledge = _ByQuery([parking])
    assistant, _ = _assistant(
        [_decision(evidence_selection=[{"item_id": 1, "answer_ids": [8001]}])], knowledge
    )
    decision = await _respond(assistant, text, plan)
    item_calls = [kwargs for _, kwargs in knowledge.calls if kwargs.get("trigger_text")]
    assert {kwargs["trigger_text"] for kwargs in item_calls} == {
        "白天停车多少钱", "晚上几点以后要保持安静",
    }
    assert all(kwargs["exclude_text"] == text for kwargs in item_calls)
    assert "20元" not in decision.reply_text


@pytest.mark.asyncio
async def test_unanswered_attractions_are_not_dropped_after_live_weather() -> None:
    """天气联网后，景点小句没被联网回答，必须交给主调用逐项作答（F3、G3）。"""
    text = "房间有吹风机吗，今天天气怎么样，武汉有哪些经典景点推荐"
    plan = plan_for(
        text,
        ("static_fact", "房间有吹风机吗"),
        ("external_info", "今天天气怎么样"),
        ("external_info", "武汉有哪些经典景点推荐"),
    )
    hairdryer = _snippet(9040, "房间有吹风机吗？", "每间房卫生间都配有吹风机。")
    assistant, completions = _assistant(
        [_decision(
            item_answers=[{"item_id": 3, "text": "经典景点有黄鹤楼、东湖和湖北省博物馆。"}]
        )],
        _Knowledge([hairdryer]),
    )
    decision = await _respond(assistant, text, plan)
    assert completions.requests, "景点没被联网覆盖，必须继续主调用"
    envelope = json.loads(completions.requests[-1]["messages"][-1]["content"])
    assert [item["item_id"] for item in envelope["other_items"]] == [3]
    assert "黄鹤楼" in decision.reply_text
    assert "22至28" in decision.reply_text


@pytest.mark.parametrize(
    ("texts", "reason", "risk", "refund"),
    [
        (("我要退款，投\n诉到平台", "我要退款，投诉到平台"), "refund", "critical", True),
        (("我要退\n款，不然投诉到平台", "我要退款，不然投诉到平台"), "refund", "critical", True),
        (("我要投诉到平台",), "complaint", "critical", False),
        (("第一次来太开心了！！！",), "agitated", "high", False),
    ],
)
def test_complaint_variants_merge_by_business_priority(texts, reason, risk, refund) -> None:
    """全部变体按退款 > 平台投诉 > 情绪词合并原因，风险取最高，退款标记任一为真（F4）。"""
    result = ComplaintService.classify_all(texts)
    assert (result.reason, result.risk_level, result.refund_or_compensation) == (
        reason, risk, refund,
    )
    assert ComplaintService.classify_all(("房间有吹风机吗",)).is_complaint is False


FLOOR_ONE = _snippet(5001, "一楼早餐厅几点开放？", "一楼早餐厅开放时间为8:00。", category="早餐")
FLOOR_TWO = _snippet(5002, "二楼早餐厅几点开放？", "二楼早餐厅开放时间为10:00。", category="早餐")


def _plan(question: str, entries, *, target: date = PLAN_TODAY):
    """按对象约束的逐项回退。"""
    return build_evidence_plan(
        question, entries,
        supporting_for_topic=DeepSeekGuestAssistant._supporting_knowledge,
        is_property_question=True, target_date=target, object_scoped=True,
    )


def test_different_floors_are_different_objects_on_both_paths() -> None:
    """一楼、二楼早餐厅不是同一对象：选择核验与逐项回退都发一楼 8:00（F5，V2-R3）。"""
    assert not same_object(FLOOR_ONE, FLOOR_TWO)
    selected = verify_selected_evidence(
        "一楼早餐厅几点开放", [5001], [], [FLOOR_ONE, FLOOR_TWO], target_date=PLAN_TODAY,
    )
    fallback = _plan("一楼早餐厅几点开放", [FLOOR_ONE, FLOOR_TWO])
    assert selected.status == "grounded" and "8:00" in selected.parts[0].text
    assert fallback.status == "grounded" and "8:00" in fallback.answers[0]


def test_same_object_conflict_and_period_rules() -> None:
    """同问法真冲突仍拦截；同对象期间政策按 D6 优先；不同对象的期间政策按 D-G8 否决。"""
    other = _snippet(5003, "一楼早餐厅几点开放？", "一楼早餐厅开放时间为9:00。", category="早餐")
    assert same_object(FLOOR_ONE, other)
    assert verify_selected_evidence(
        "一楼早餐厅几点开放", [5001], [], [FLOOR_ONE, other], target_date=PLAN_TODAY,
    ).status == "missing"
    assert _plan("一楼早餐厅几点开放", [FLOOR_ONE, other]).status == "insufficient"

    holiday_same = _snippet(
        5004, "一楼早餐厅几点开放？", "一楼早餐厅国庆期间开放时间为9:30。", category="早餐",
        valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 7),
    )
    verdict = verify_selected_evidence(
        "一楼早餐厅几点开放", [5001], [], [FLOOR_ONE, holiday_same], target_date=PLAN_TODAY,
    )
    assert verdict.status == "grounded" and "9:30" in verdict.parts[0].text

    holiday_other = replace(
        FLOOR_TWO, valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 7)
    )
    vetoed = verify_selected_evidence(
        "一楼早餐厅几点开放", [5001], [], [FLOOR_ONE, holiday_other], target_date=PLAN_TODAY,
    )
    assert (vetoed.status, vetoed.reason) == ("missing", "period_veto")
    assert _plan("一楼早餐厅几点开放", [FLOOR_ONE, holiday_other]).reason == "period_veto"


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        ("民宿免费健身房几点开放", "民宿收费健身房几点开放", False),
        ("包含早餐的套餐", "不包含早餐的套餐", False),
        ("健身房几点开门？", "健身房开放时间？", False),
        ("Can I park here?", "can i park here", True),
        ("请问早餐几点？", "早餐几点", True),
    ],
)
def test_object_key_keeps_qualifiers(left: str, right: str, same: bool) -> None:
    """只删纯疑问与语气形式：收费限定、否定保留；不同措辞的同对象属已声明上限（C2）。"""
    assert (object_question_key(left) == object_question_key(right)) is same


def _availability_row(property_id: int, start: str, end: str) -> dict[str, Any]:
    """一行可订房态。"""
    return {"property_id": property_id, "property_title": str(property_id - 1000),
            "check_in_date": start, "check_out_date": end, "stay_available": True}


@pytest.mark.asyncio
async def test_coarse_item_reports_each_uncovered_fact() -> None:
    """粗粒度一项问价格、房态和早餐：只查到房态时，价格单独回未查到，早餐按审核知识作答（V3-R1）。"""
    text = "10月10日401房间住一晚多少钱，有房吗，早餐几点开始"
    plan = plan_for(text, ("stay_query", text, {
        "target_room": "401", "check_in_date": "2026-10-10", "check_out_date": "2026-10-11",
    }))
    breakfast = _snippet(6001, "早餐几点开始？", "早餐7:30开始。", category="早餐")
    arguments = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-11"}
    tools = _Tools({"search_availability": [_availability_row(1401, "2026-10-10", "2026-10-11")]})
    assistant, _ = _assistant(
        [_tool_message("search_availability", arguments), _decision(reply_text="都可以。")],
        _Knowledge([breakfast], {"401": 1401}), tools,
    )
    decision = await _respond(assistant, text, plan)
    assert "可订" in decision.reply_text
    assert "参考价" in decision.reply_text and "没有查到" in decision.reply_text
    assert "7:30" in decision.reply_text
    assert "都可以" not in decision.reply_text


@pytest.mark.asyncio
async def test_other_named_room_is_not_covered_by_the_first_room() -> None:
    """一项点名 401 与 402，只查到 401 时，402 单独回未查到（V4-R1）。"""
    text = "10月10日401有房吗，402有房吗"
    plan = plan_for(text, ("stay_query", text, {
        "target_room": "401", "check_in_date": "2026-10-10", "check_out_date": "2026-10-11",
    }))
    arguments = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-11"}
    tools = _Tools({"search_availability": [_availability_row(1401, "2026-10-10", "2026-10-11")]})
    assistant, _ = _assistant(
        [_tool_message("search_availability", arguments), _decision()],
        _Knowledge([], {"401": 1401, "402": 1402}), tools,
    )
    decision = await _respond(assistant, text, plan)
    assert "「402有房吗」这次没有查到对应的房态" in decision.reply_text
    assert any(unit.reason == "tool_uncovered" for unit in decision.gap_units)


@pytest.mark.asyncio
async def test_misclassified_catalog_keeps_the_gym_gap() -> None:
    """健身房时间被误分为目录类：目录结果不能替掉未确认（V3-R2，D-G9）。"""
    text = "你们的健身房几点开放"
    plan = plan_for(text, ("catalog_query", text))
    tools = _Tools({"list_properties": [{"id": 1401, "title": "401房间"}]})
    assistant, _ = _assistant(
        [_tool_message("list_properties", {}), _decision(reply_text="健身房早上8点开放。")],
        _Knowledge([]), tools,
    )
    decision = await _respond(assistant, text, plan)
    assert UNCONFIRMED in decision.reply_text
    assert "8点" not in decision.reply_text
    assert any(unit.reason == "knowledge_missing" for unit in decision.gap_units)


@pytest.mark.asyncio
async def test_availability_without_dates_asks_for_dates() -> None:
    """无日期问房态、也没有任何房态结果时追问入住日期（D-G10）。"""
    text = "401有房吗"
    plan = plan_for(text, ("stay_query", text, {"target_room": "401"}))
    assistant, _ = _assistant([_decision(reply_text="有的。")], _Knowledge([], {"401": 1401}))
    decision = await _respond(assistant, text, plan)
    assert "哪天入住" in decision.reply_text and "房态" in decision.reply_text
    assert "有的" not in decision.reply_text


def test_missing_nights_are_named_in_the_price_part() -> None:
    """两晚只查到一晚参考价时，正文写明哪一晚未查到（V6-R3）。"""
    arguments = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-12"}
    parts = DeepSeekGuestAssistant._tool_reply_parts(
        "search_reference_price", arguments,
        [{"property_id": 1401, "property_title": "401房间", **arguments,
          "nightly_reference_prices": [{"date": "2026-10-10", "price": 300}]}],
        Language.ZH, "tool-p", "10月10日入住12日退房，两晚多少钱",
    )
    assert "10月11日的参考价暂未查到" in parts[0].text


@pytest.mark.asyncio
async def test_failed_main_call_after_tools_keeps_the_item_contract() -> None:
    """工具成功后主模型两次都失败：回执仍逐项取证并报告缺口，不拼整轮证据（availability_fallback）。"""
    text = "10月10日401房间住一晚多少钱，有房吗，早餐几点开始"
    plan = plan_for(text, ("stay_query", text, {
        "target_room": "401", "check_in_date": "2026-10-10", "check_out_date": "2026-10-11",
    }))
    breakfast = _snippet(6001, "早餐几点开始？", "早餐7:30开始。", category="早餐")
    arguments = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-11"}
    tools = _Tools({"search_availability": [_availability_row(1401, "2026-10-10", "2026-10-11")]})
    assistant, _ = _assistant(
        [_tool_message("search_availability", arguments), "不是JSON"],
        _Knowledge([breakfast], {"401": 1401}), tools,
    )
    decision = await _respond(assistant, text, plan)
    assert "可订" in decision.reply_text
    assert "参考价" in decision.reply_text and "没有查到" in decision.reply_text
    assert "7:30" in decision.reply_text


class _ItemNarrowKnowledge(_Knowledge):
    """整句检索多带一条别的条目，逐项检索（带本项摘录）只返回本项条目。"""

    def __init__(self, item_entries, extra_entries):
        super().__init__([*item_entries, *extra_entries])
        self.item_entries = item_entries

    async def retrieve(self, language, query, **kwargs):
        self.calls.append((query, kwargs))
        return self.item_entries if kwargs.get("trigger_text") else self.entries


@pytest.mark.asyncio
async def test_related_id_outside_item_candidates_does_not_void_a_legal_answer() -> None:
    """模型答本项合法条目、相关编号指向别处可见的条目：照常作答，不整条判越界。

    1.70.0 候选门禁 K-访客：答 9033（访客）、相关 9032（安静时段）被判越界后回未确认。
    """
    text = "朋友晚上能来房间坐坐吗"
    visitor = _snippet(9033, "朋友可以来房间坐坐吗？", "访客可以在22:00前来访。", category="访客")
    quiet = _snippet(9032, "晚上几点以后要保持安静？", "每天22:00至次日8:00是安静时段。")
    assistant, _ = _assistant(
        [_decision(evidence_selection=[
            {"item_id": 1, "answer_ids": [9033], "related_ids": [9032]}
        ])],
        _ItemNarrowKnowledge([visitor], [quiet]),
    )
    decision = await _respond(assistant, text, plan_for(text, ("static_fact", text)))
    assert "22:00前来访" in decision.reply_text
    assert UNCONFIRMED not in decision.reply_text


@pytest.mark.asyncio
async def test_voltage_question_is_not_a_room_number() -> None:
    """「插座是220V的吗」不是问 220 房间：按审核资料作答，不判范围未绑定。"""
    text = "插座是220V的吗"
    socket = _snippet(9034, "插座是什么规格？", "房间插座是220V国标两孔和三孔。", category="用电")
    assistant, _ = _assistant(
        [_decision(evidence_selection=[{"item_id": 1, "answer_ids": [9034]}])],
        _Knowledge([socket]),
    )
    decision = await _respond(assistant, text, plan_for(text, ("static_fact", text)))
    assert "220V国标" in decision.reply_text


LAUNDRY_FREE = _snippet(
    9017, "有洗衣机吗？在哪里、几点能用？", "一楼洗衣房的洗衣机免费使用。", category="洗衣"
)
LAUNDRY_PAID = _snippet(
    9018, "洗衣和烘干要收费吗？", "洗衣机每次10元，烘干机每次15元。", category="洗衣"
)


@pytest.mark.parametrize(
    ("answers", "related"),
    [([9017], [9018]), ([9017, 9018], [])],
)
def test_model_flagged_entries_veto_contradicting_fee(answers, related) -> None:
    """模型选为回答或标为相关的条目在所问收费上矛盾：回未确认（用户 2026-10-08 决定）。"""
    verdict = verify_selected_evidence(
        "洗衣机要钱吗", answers, related, [LAUNDRY_FREE, LAUNDRY_PAID],
    )
    assert verdict.status == "missing" and verdict.reason.startswith("conflict_flagged")


def test_unflagged_other_object_is_still_not_a_conflict() -> None:
    """对照：模型没标另一条时按不同对象各自作答（已声明的上限），一楼早餐不受二楼影响。"""
    verdict = verify_selected_evidence("洗衣机要钱吗", [9017], [], [LAUNDRY_FREE, LAUNDRY_PAID])
    assert verdict.status == "grounded"
    floor = verify_selected_evidence(
        "一楼早餐厅几点开放", [5001], [], [FLOOR_ONE, FLOOR_TWO], target_date=PLAN_TODAY,
    )
    assert floor.status == "grounded"


@pytest.mark.asyncio
async def test_service_fee_followup_is_answered_as_static_fact_even_if_planned_as_stay() -> None:
    """「Do you have parking?」后问「How much is it?」，规划误判为住宿查询：仍按停车收费作答。

    1.70.0 候选门禁 MT-停车EN：逐项模式只认规划标签，回了「这次没有查到参考价」。
    """
    parking = _snippet(
        9010, "停车怎么收费？", "芸栖路公共停车场每小时10元，24小时内最高60元。",
        category="停车",
    )
    text = "How much is it?"
    plan = plan_for(text, ("stay_query", text))
    assistant, _ = _assistant(
        [_decision(evidence_selection=[{"item_id": 1, "answer_ids": [9010]}])],
        _Knowledge([parking]),
    )
    decision = await assistant.respond(
        guest_identifier="g", language=Language.EN, turn_plan=plan,
        messages=[
            {"role": "user", "content": "Do you have parking?"},
            {"role": "assistant", "content": "You can use the Yunqi Road public car park."},
            {"role": "user", "content": text},
        ],
    )
    assert "10元" in decision.reply_text
    assert "reference price" not in decision.reply_text


QUIET_TEXT = "你们有安静时段规定吗，我们想晚上在房间聚一下"
QUIET_PLAN_ITEMS = (
    ("static_fact", "你们有安静时段规定吗"),
    ("static_fact", "我们想晚上在房间聚一下", {"question": "晚上可以在房间聚一下吗"}),
)


class _PerItemKnowledge(_Knowledge):
    """逐项检索按问法返回预置条目；整句检索返回全部。"""

    def __init__(self, by_question: dict[str, list[KnowledgeSnippet]]):
        super().__init__([entry for entries in by_question.values() for entry in entries])
        self.by_question = by_question

    async def retrieve(self, language, query, **kwargs):
        self.calls.append((query, kwargs))
        if kwargs.get("trigger_text"):
            return self.by_question.get(query, [])
        return list({id(entry): entry for entry in self.entries}.values())


def _quiet_assistant(quiet: KnowledgeSnippet, visitor: KnowledgeSnippet):
    """第一项只检到安静时段，第二项只检到访客规定；模型给第二项选两条。"""
    return _assistant(
        [_decision(evidence_selection=[
            {"item_id": 1, "answer_ids": [quiet.source_id]},
            {"item_id": 2, "answer_ids": [quiet.source_id, visitor.source_id]},
        ])],
        _PerItemKnowledge({"你们有安静时段规定吗": [quiet], "晚上可以在房间聚一下吗": [visitor]}),
    )


VISITOR = _snippet(9033, "朋友可以来房间坐坐吗？", "访客可以在22:00前来访。", category="访客")


@pytest.mark.asyncio
async def test_calendar_binding_keeps_the_turn_clock_across_midnight():
    """检索等待跨午夜后仍按本轮开始的今日取明天，不能临时把明天推迟一天。"""
    text = "明天早餐几点开始"
    entry = _snippet(
        9608, "早餐几点开始？", "早餐8:30开始。", category="早餐",
        valid_from=date(2026, 10, 8), valid_until=date(2026, 10, 8),
    )
    clock = [PLAN_TODAY]

    class MidnightKnowledge(_Knowledge):
        """在真实助手的检索等待点推进合成时钟，不改变取证候选或规划内容。"""

        async def retrieve(self, language, query, **kwargs):
            """检索开始后跨午夜；当天已冻结的计划仍指向10月8日。"""
            clock[0] = date(2026, 10, 8)
            return await super().retrieve(language, query, **kwargs)

    assistant, _ = _assistant([_decision()], MidnightKnowledge([entry]))
    assistant._local_date_provider = lambda: clock[0]
    plan = plan_for(text, ("static_fact", text, {
        "check_in_date": "2026-10-08", "check_out_date": "2026-10-09",
    }))
    decision = await _respond(assistant, text, plan)
    assert "8:30" in decision.reply_text
    assert not decision.gap_units


@pytest.mark.asyncio
@pytest.mark.parametrize("planned_day", [None, 10, 9])
@pytest.mark.parametrize("fallback", [False, True])
async def test_independent_weekday_keeps_approved_exception(planned_day, fallback):
    """独立周六政策问题自行定日；正确计划与协议回退保留例外，错误计划不能被悄悄纠正。"""
    text = "这周六能延迟退房到下午两点吗？"
    knowledge = _Knowledge([_snippet(
        9003, "可以延迟退房吗？怎么收费？", "每周六不提供延迟退房。", category="入住退房",
    )])
    fields = {} if planned_day is None else {
        "check_in_date": f"2026-10-{planned_day:02d}",
        "check_out_date": f"2026-10-{planned_day + 1:02d}",
    }
    if fallback:
        first = "10月7日入住10月9日退房有房吗"
        args = {"check_in_date": "2026-10-07", "check_out_date": "2026-10-09"}
        items = [("stay_query", first, args), ("static_fact", text, fields)]
        text = first + "，" + text
        assistant, _ = _assistant([_tool_message("search_availability", args), "invalid"],
                                  knowledge, _Tools({"search_availability": [
                                      _availability_row(1401, *args.values()),
                                  ]}))
    else:
        items = [("static_fact", text, fields)]
        assistant, _ = _assistant([_decision()], knowledge)
    decision = await _respond(assistant, text, plan_for(text, *items))
    if planned_day == 9:
        assert "不提供延迟退房" not in decision.reply_text
        assert [g.reason for g in decision.gap_units] == ["scope_unbound"]
    else:
        assert "每周六不提供延迟退房" in decision.reply_text
        assert not decision.gap_units
        _, params = knowledge.calls[-1]
        assert params["target_date"] == date(2026, 10, 10)
        assert params["target_end_date"] == date(2026, 10, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("second", ["明天早餐几点开始", "第二天早餐几点开始"])
async def test_shared_entry_cannot_lend_today_to_another_relative_day(second):
    """今天已核验的全店资料不能借给明天或无锚点第二天，即使模型明确选了同一编号。"""
    first = "今天早餐几点开始"
    text = first + "，" + second
    entry = _snippet(
        9607, "早餐几点开始？", "早餐7:30开始。", category="早餐",
        valid_from=PLAN_TODAY, valid_until=PLAN_TODAY,
    )
    assistant, _ = _assistant([_decision(evidence_selection=[
        {"item_id": 1, "answer_ids": [9607]}, {"item_id": 2, "answer_ids": [9607]},
    ])], _PerItemKnowledge({first: [entry], second: []}))
    plan = plan_for(text, ("static_fact", first, {
        "check_in_date": "2026-10-07", "check_out_date": "2026-10-08",
    }), ("static_fact", second, {
        "check_in_date": "2026-10-08", "check_out_date": "2026-10-09",
    }))
    decision = await _respond(assistant, text, plan)
    assert decision.reply_text.count("7:30") == 1
    assert [(g.text, g.reason) for g in decision.gap_units] == [
        (second, "knowledge_missing" if second.startswith("明天") else "scope_unbound")
    ]


@pytest.mark.asyncio
async def test_entry_verified_for_another_item_can_answer_this_item() -> None:
    """同轮另一项已核验的全店通用条目，模型又选它回答本项：本项作答，正文不重复（K-安静-C）。"""
    quiet = _snippet(9032, "晚上几点以后要保持安静？", "22:00后是安静时段，本店不接待聚会。")
    assistant, _ = _quiet_assistant(quiet, VISITOR)
    decision = await _respond(assistant, QUIET_TEXT, plan_for(QUIET_TEXT, *QUIET_PLAN_ITEMS))
    assert decision.reply_text.count("不接待聚会") == 1
    assert "访客可以在22:00前来访" in decision.reply_text
    assert UNCONFIRMED not in decision.reply_text


@pytest.mark.parametrize(
    "fields",
    [{"scope": "property", "property_id": 1401}, {"conditional": True}],
)
@pytest.mark.asyncio
async def test_room_specific_or_conditional_entries_are_not_shared(fields) -> None:
    """对照：房间专属或带附加条件词的条目不跨项复用，第二项仍回未确认（B24-R3、G2）。"""
    quiet = _snippet(
        9032, "晚上几点以后要保持安静？", "22:00后是安静时段，本店不接待聚会。", **fields
    )
    assistant, _ = _quiet_assistant(quiet, VISITOR)
    context = AssistantRequestContext(property_id=1401) if "property_id" in fields else None
    decision = await _respond(
        assistant, QUIET_TEXT, plan_for(QUIET_TEXT, *QUIET_PLAN_ITEMS),
        **({"request_context": context} if context else {}),
    )
    assert UNCONFIRMED in decision.reply_text


@pytest.mark.asyncio
async def test_retrieval_marks_entries_with_trigger_conditions() -> None:
    """真实检索把配置了附加条件词的条目标为 conditional，跨项复用据此排除。"""
    from homestay_bot.services.knowledge_service import KnowledgeService

    def record(entry_id: int, question: str, answer: str, trigger_any=None) -> SimpleNamespace:
        return SimpleNamespace(
            id=entry_id, category="停车", scope="global", property_id=None,
            valid_from=None, valid_until=None, question_zh=question, answer_zh=answer,
            question_en="parking", answer_en="parking", keywords=["停车"],
            trigger_any=trigger_any, trigger_exclude=None,
        )

    class _Repository:
        async def list_active(self):
            return [
                record(1, "停车多少钱？", "每小时10元。"),
                record(2, "晚上停车多少钱？", "晚上每小时5元。", ["晚上"]),
            ]

    found = await KnowledgeService(_Repository()).retrieve(
        Language.ZH, "晚上停车多少钱", trigger_text="晚上停车多少钱"
    )
    flags = {snippet.source_id: snippet.conditional for snippet in found}
    assert flags == {1: False, 2: True}


BREAKFAST = _snippet(9701, "早餐几点开始？", "早餐7:30开始。", category="早餐")


def _weekend_breakfast(source_id: int, answer: str, day: int) -> KnowledgeSnippet:
    """只在 2026-10-{day} 当天有效的早餐条目。"""
    return _snippet(
        source_id, "早餐几点开始？", answer, category="早餐",
        valid_from=date(2026, 10, day), valid_until=date(2026, 10, day),
    )


async def _weekend_reply(text: str, entries, fallback: bool, answer_ids=(9701,)):
    """周末静态问答：正常出口或「先查房、再回退」出口，模型选择相同。"""
    knowledge = _Knowledge(entries)
    selection = [{"item_id": 2 if fallback else 1, "answer_ids": list(answer_ids)}]
    if fallback:
        first = "10月7日入住10月9日退房有房吗"
        args = {"check_in_date": "2026-10-07", "check_out_date": "2026-10-09"}
        items = [("stay_query", first, args), ("static_fact", text, {})]
        text = first + "，" + text
        assistant, _ = _assistant(
            [_tool_message("search_availability", args), "invalid"], knowledge,
            _Tools({"search_availability": [_availability_row(1401, *args.values())]}),
        )
    else:
        items = [("static_fact", text, {})]
        assistant, _ = _assistant([_decision(evidence_selection=selection)], knowledge)
    return await _respond(assistant, text, plan_for(text, *items)), knowledge


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_weekend_static_question_uses_unlimited_policy(fallback):
    """无期限资料下「这周末早餐几点」直接作答，检索目标是本周六至周日（RV-1）。"""
    decision, knowledge = await _weekend_reply("这周末早餐几点开始", [BREAKFAST], fallback)
    assert "早餐7:30开始" in decision.reply_text
    assert not decision.gap_units
    _, params = knowledge.calls[-1]
    assert (params["target_date"], params["target_end_date"]) == (
        date(2026, 10, 10), date(2026, 10, 11)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_weekend_same_object_exception_is_answered_per_day(fallback):
    """同一问法的周六例外按天分段，日期与时刻对应，不混用（D-W6 单日写法）。"""
    entries = [BREAKFAST, _weekend_breakfast(9702, "早餐9:30开始。", 10)]
    decision, _ = await _weekend_reply("这周末早餐几点开始", entries, fallback)
    assert "2026-10-10：早餐9:30开始。" in decision.reply_text
    assert "2026-10-11：早餐7:30开始。" in decision.reply_text
    assert not decision.gap_units
    by_day = {
        evidence.target_date: (evidence.source_id, part.text)
        for part in decision.reply_parts
        for evidence in part.evidence
    }
    assert by_day[date(2026, 10, 10)][0] == "9702" and "9:30" in by_day[date(2026, 10, 10)][1]
    assert by_day[date(2026, 10, 11)][0] == "9701" and "7:30" in by_day[date(2026, 10, 11)][1]


@pytest.mark.asyncio
async def test_weekend_missing_day_is_dated_and_referable():
    """周六有资料、周日没有：周六照答，周日带日期说明未确认，缺口仍可转交。"""
    entries = [_weekend_breakfast(9703, "早餐8:30开始。", 10)]
    decision, _ = await _weekend_reply("这周末早餐几点开始", entries, False, (9703,))
    assert "2026-10-10：早餐8:30开始。" in decision.reply_text
    assert "2026-10-11：当前审核资料尚未确认早餐信息" in decision.reply_text
    assert [gap.reason for gap in decision.gap_units] == ["knowledge_missing"]
    assert decision.knowledge_gap_topic == "早餐时间"


@pytest.mark.asyncio
async def test_weekend_conflict_on_one_day_is_not_referable():
    """周六正常、周日资料冲突：缺口原因是冲突，不能当资料缺失转交（W-R2）。"""
    entries = [
        _weekend_breakfast(9704, "早餐7:30开始。", 10),
        _weekend_breakfast(9705, "早餐9:30开始。", 11),
        _weekend_breakfast(9706, "早餐10:30开始。", 11),
    ]
    decision, _ = await _weekend_reply("这周末早餐几点开始", entries, False, (9704,))
    assert "2026-10-10：早餐7:30开始。" in decision.reply_text
    assert [gap.reason for gap in decision.gap_units] == ["conflict"]


@pytest.mark.asyncio
async def test_weekend_outside_coarse_stay_stays_unbound():
    """粗住宿项范围外的周末残留不借住宿日期，也不扩大检索；不作答、不转交。"""
    text = "10月7日入住10月9日退房有房吗，周末早餐几点开始"
    args = {"check_in_date": "2026-10-07", "check_out_date": "2026-10-09"}
    knowledge = _Knowledge([BREAKFAST])
    assistant, _ = _assistant(
        [_tool_message("search_availability", args), _decision()], knowledge,
        _Tools({"search_availability": [_availability_row(1401, *args.values())]}),
    )
    decision = await _respond(assistant, text, plan_for(text, ("stay_query", text, args)))
    assert "7:30" not in decision.reply_text
    assert [gap.reason for gap in decision.gap_units] == ["scope_unbound"]


def test_dated_door_credential_part_is_still_refused_not_referred():
    """带日期前缀的门锁凭证缺口仍是专门拒绝（out_of_scope），日期只加在正文前。"""
    part = ReplyPart(question="2026-10-11：门锁凭证", status="missing", text="")
    refused = DeepSeekGuestAssistant._unconfirmed_part(part, Language.ZH)
    assert refused.status == "out_of_scope"
    assert refused.text.startswith("2026-10-11：聊天里不能提供或转发门锁密码")
    english = DeepSeekGuestAssistant._unconfirmed_part(
        ReplyPart(question="2026-10-11：早餐时间", status="missing", text=""), Language.EN
    )
    assert english.status == "missing"
    assert english.text.startswith("2026-10-11：Our reviewed information hasn't confirmed")


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("text", ["下周的周末早餐几点开始", "上一个周末早餐几点开始"])
async def test_foreign_weekend_qualifier_never_answers_this_weekend(text, fallback):
    """未支持的周末限定不按本周作答：只在本周有效的资料零来源、零转交（I71-R1）。"""
    entries = [
        _weekend_breakfast(9707, "早餐8:30开始。", 10),
        _weekend_breakfast(9708, "早餐9:00开始。", 11),
    ]
    decision, _ = await _weekend_reply(text, entries, fallback, (9707, 9708))
    assert "8:30" not in decision.reply_text and "9:00" not in decision.reply_text
    assert not [
        e for part in decision.reply_parts for e in part.evidence if e.source_kind == "knowledge"
    ]
    assert all(gap.reason != "knowledge_missing" for gap in decision.gap_units)


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["房间能抽菸吗", "房间可以吸菸吗", "可以抽一根菸吗"])
@pytest.mark.parametrize("has_source", [False, True])
async def test_smoking_variant_without_model_selection_uses_only_approved_source(
    question: str, has_source: bool,
) -> None:
    """模型保留异体字且未选编号时仍可取证；不靠模型改字，也不凭空补禁烟政策。"""
    answer = "房间和阳台禁止吸烟，需要吸烟请到院外指定区域。"
    sources = [_snippet(9721, "房间可以抽烟吗？", answer)] if has_source else []
    assistant, _client = _assistant([_decision()], knowledge=_Knowledge(sources))
    outcome = plan_for(question, ("static_fact", question))
    decision = await _respond(assistant, question, outcome)

    if has_source:
        assert answer in decision.reply_text
        assert {e.source_id for part in decision.reply_parts for e in part.evidence} == {"9721"}
        assert UNCONFIRMED not in decision.reply_text
    else:
        assert UNCONFIRMED in decision.reply_text
        assert "禁止吸烟" not in decision.reply_text


# ---- 本店事实／店外信息合同（规划误判 Spec §12，RV177-1、RV177-3） ----

_WEB_TEXT = "网上搜到：某商场可租借。"


def _system_prompt(completions) -> str:
    """主调用的系统提示；本轮没有主调用时返回空串。"""
    return completions.requests[0]["messages"][0]["content"] if completions.requests else ""


@pytest.mark.asyncio
@pytest.mark.parametrize("question,answer", [
    ("今天可以借吹风机吗", "每间房都配有吹风机，在卫生间抽屉里。"),
    ("现在有雨伞可以借吗", "前台可免费借用雨伞，用完放回门口伞架。"),
    ("民宿离地铁站多远？", "民宿距6号线芸栖路站约450米，步行约6分钟。"),
    ("公共客厅几点开放？", "一楼公共客厅8:00至22:00开放。"),
])
@pytest.mark.parametrize("has_source", [True, False])
async def test_property_fact_with_live_words_is_answered_from_knowledge_not_search(
    question: str, answer: str, has_source: bool,
) -> None:
    """计划判为本店事实时，「今天」「几点开放」「多远」等词面信号不能把整句送去联网：
    有审核资料按来源回答，无资料回答未确认，都不出现网页结果（RV177-3）。"""
    sources = [_snippet(9801, question, answer)] if has_source else []
    tourism = _Tourism({question.rstrip("？"): _WEB_TEXT})
    # 模型选中审核编号：本用例只验证联网分流，不验证无选择时的主题后备取证。
    selection = [{"item_id": 1, "answer_ids": [9801]}] if has_source else []
    assistant, _completions = _assistant(
        [_decision(evidence_selection=selection)], knowledge=_Knowledge(sources), tourism=tourism
    )
    decision = await _respond(assistant, question, plan_for(question, ("static_fact", question)))

    assert tourism.questions == []
    assert _WEB_TEXT not in decision.reply_text
    if has_source:
        assert answer in decision.reply_text
        assert UNCONFIRMED not in decision.reply_text
    else:
        assert UNCONFIRMED in decision.reply_text


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["今天可以借吹风机吗", "现在有雨伞可以借吗"])
async def test_borrow_request_with_time_word_is_not_searched(question: str) -> None:
    """借用被规划为服务请求时同样不联网，交回主回复（RV177-3）。"""
    tourism = _Tourism({question: _WEB_TEXT})
    assistant, completions = _assistant([_decision()], tourism=tourism)
    decision = await _respond(
        assistant, question, plan_for(question, ("service_request", question))
    )

    assert tourism.questions == []
    assert _WEB_TEXT not in decision.reply_text
    assert completions.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_kind", [None, "failed", "mismatch"])
async def test_unusable_plan_keeps_lexical_live_search(plan_kind: str | None) -> None:
    """计划不可用时联网维持词面判断，天气等真实店外问题不会因此失去实时数据（D12-2）。"""
    question = "今天可以借吹风机吗"
    if plan_kind is None:
        plan = None
    elif plan_kind == "failed":
        plan = failed_plan(question, "schema")
    else:
        plan = plan_for("别的正文", ("static_fact", "别的正文"))
    tourism = _Tourism({question: _WEB_TEXT})
    assistant, completions = _assistant([_decision()], tourism=tourism)
    await _respond(assistant, question, plan)

    assert tourism.questions == [question]
    assert _STABLE_RECOMMENDATION_RULE not in _system_prompt(completions)


@pytest.mark.asyncio
@pytest.mark.parametrize("question,items,searched", [
    ("武汉明天天气怎么样", [("external_info", "武汉明天天气怎么样")], ["武汉明天天气怎么样"]),
    (
        "前台有什么玩具可以借吗，武汉明天天气怎么样",
        [("service_request", "前台有什么玩具可以借吗"), ("external_info", "武汉明天天气怎么样")],
        ["武汉明天天气怎么样"],
    ),
    # 「还有」不切句，整句词面不是实时（v1.70.6、v1.70.7 都不联网），合同不让它变成联网。
    (
        "前台有什么玩具可以借吗还有武汉明天天气怎么样",
        [("service_request", "前台有什么玩具可以借吗"), ("external_info", "武汉明天天气怎么样")],
        [],
    ),
])
async def test_external_info_clause_keeps_search(
    question: str, items: list[tuple[str, str]], searched: list[str],
) -> None:
    """落在店外信息事项内的实时小句照常联网；同轮本店小句不被带进联网。"""
    tourism = _Tourism({clause: "明天武汉多云。" for clause in searched})
    assistant, _completions = _assistant([_decision()], tourism=tourism)
    await _respond(assistant, question, plan_for(question, *items))

    assert tourism.questions == searched


@pytest.mark.asyncio
@pytest.mark.parametrize("question,items,expected", [
    # 店内设施的「玩」字问法：计划判为本店事项时不追加推荐约束（RV177-1）。
    ("民宿有啥玩具吗", [("static_fact", "民宿有啥玩具吗")], False),
    ("前台有什么玩具可以借吗", [("service_request", "前台有什么玩具可以借吗")], False),
    ("房间有什么玩的", [("static_fact", "房间有什么玩的")], False),
    ("电视有什么玩的频道", [("static_fact", "电视有什么玩的频道")], False),
    ("民宿有什么好玩的玩具", [("static_fact", "民宿有什么好玩的玩具")], False),
    # 店外推荐：W-推荐、W-小吃、W-天气玩法（一项跨两个小句）的门禁形状。
    ("你们推荐什么景点？", [("external_info", "你们推荐什么景点？")], True),
    ("武汉有什么必吃的小吃？", [("external_info", "武汉有什么必吃的小吃？")], True),
    ("武汉最近有啥玩的，天气咋样", [("external_info", "武汉最近有啥玩的，天气咋样")], True),
    ("房间附近有什么玩的", [("external_info", "房间附近有什么玩的")], True),
    # 推荐信号不在店外事项自己的原文里，不能借用（Codex「还有」反例）。
    (
        "前台有什么玩具可以借吗还有武汉明天天气怎么样",
        [("service_request", "前台有什么玩具可以借吗"), ("external_info", "武汉明天天气怎么样")],
        False,
    ),
    (
        "前台有什么玩具可以借吗另外武汉明天天气怎么样",
        [("service_request", "前台有什么玩具可以借吗"), ("external_info", "武汉明天天气怎么样")],
        False,
    ),
])
async def test_recommendation_rule_requires_external_info_quote(
    question: str, items: list[tuple[str, str]], expected: bool,
) -> None:
    """推荐生成约束只在店外信息事项自己的原文里有普通推荐小句时追加（Spec §12 F12-1）。"""
    tourism = _Tourism({"武汉最近有啥玩的，天气咋样": "明天武汉多云。"})
    assistant, completions = _assistant([_decision()], tourism=tourism)
    await _respond(assistant, question, plan_for(question, *items))

    assert (_STABLE_RECOMMENDATION_RULE in _system_prompt(completions)) is expected
