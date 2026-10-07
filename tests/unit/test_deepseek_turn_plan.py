"""适配器内的轮次计划：规划、复用、随异常带回、工具并集与证据选择（Spec §2.3、§2.4）。"""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.integrations import deepseek_client
from homestay_bot.integrations.deepseek_client import (
    AssistantUnavailableError,
    DeepSeekGuestAssistant,
)
from homestay_bot.services.knowledge_evidence_policy import verify_selected_evidence
from homestay_bot.services.knowledge_service import KnowledgeSnippet
from tests.plan_helpers import PLAN_TODAY, plan_for

HAIRDRYER = KnowledgeSnippet(
    source_id=9040,
    category="设施",
    question="房间有吹风机吗？",
    answer="每间房卫生间都配有吹风机，放在洗手台下方抽屉里。",
    scope="global",
)


def _decision(**fields) -> str:
    """主调用返回的最小合法决定 JSON。"""
    return json.dumps(
        {"reply_text": "好的。", "language": "zh", "intent": "faq", "confidence": 0.9, **fields},
        ensure_ascii=False,
    )


def _plan_json(text: str, *items: tuple[str, str]) -> str:
    """模型规划返回的 JSON：摘录取自原文。"""
    return json.dumps(
        {
            "items": [
                {"id": index, "kind": kind, "quote": quote, "start": text.find(quote)}
                for index, (kind, quote) in enumerate(items, start=1)
            ]
        },
        ensure_ascii=False,
    )


class _Completions:
    """按顺序返回预置内容；内容为异常时抛出，记录每次请求。"""

    def __init__(self, contents: list[object]) -> None:
        """保存每次请求的返回。"""
        self.contents = contents
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        """返回无工具调用的消息。"""
        self.requests.append(kwargs)
        content = self.contents[min(len(self.requests) - 1, len(self.contents) - 1)]
        if isinstance(content, BaseException):
            raise content
        message = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _Knowledge:
    """固定返回审核知识，并记录检索参数。"""

    def __init__(self, entries: list[KnowledgeSnippet], rooms: dict[str, int] | None = None):
        """保存条目与房号映射。"""
        self.entries = entries
        self.rooms = rooms or {}
        self.calls: list[tuple[str, dict]] = []

    async def retrieve(self, language, query, **kwargs):
        """返回全部条目。"""
        self.calls.append((query, kwargs))
        return list(self.entries)

    async def find_property_by_room(self, room: str) -> int | None:
        """按房号映射房源。"""
        return self.rooms.get(room)


class _Tourism:
    """不应被调用的联网搜索。"""

    async def search(self, **kwargs):
        """测试里不联网。"""
        raise AssertionError("不应联网")


def _assistant(contents: list[object], knowledge=None, *, plan_turns: bool = True):
    """构造带规划的适配器与它的请求记录。"""
    completions = _Completions(contents)
    assistant = DeepSeekGuestAssistant(
        chat_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        tourism_searcher=_Tourism(),
        knowledge=knowledge or _Knowledge([]),
        model="deepseek-v4-flash",
        safety_hmac_key=b"test-key",
        local_date_provider=lambda: PLAN_TODAY,
        plan_turns=plan_turns,
    )
    return assistant, completions


def _is_planner(request: dict) -> bool:
    """规划请求用独立的规划提示。"""
    return "意图规划器" in request["messages"][0]["content"]


@pytest.mark.asyncio
async def test_plan_turn_verifies_the_model_output() -> None:
    """规划结果经本地核验；调用异常与超时都返回失败结果而不抛出。"""
    text = "房间有吹风机吗"
    assistant, _ = _assistant([_plan_json(text, ("static_fact", text))])
    outcome = await assistant.plan_turn(text=text, language=Language.ZH)
    assert outcome.ok and outcome.matches(text)

    broken, _ = _assistant([RuntimeError("network")])
    assert (await broken.plan_turn(text=text, language=Language.ZH)).reason == "call_failed"


@pytest.mark.asyncio
async def test_plan_turn_times_out_as_a_planning_failure(monkeypatch) -> None:
    """规划超时（D5）按规划失败处理。"""

    class _Slow(_Completions):
        async def create(self, **kwargs):
            await asyncio.sleep(1)
            return await super().create(**kwargs)

    monkeypatch.setattr(
        deepseek_client,
        "MODEL_BUDGET",
        replace(deepseek_client.MODEL_BUDGET, planning_timeout_seconds=0.01),
    )
    assistant, _ = _assistant([])
    assistant._chat_client = SimpleNamespace(chat=SimpleNamespace(completions=_Slow([])))
    assert (await assistant.plan_turn(text="早餐", language=Language.ZH)).reason == "timeout"


@pytest.mark.asyncio
async def test_respond_plans_before_retrieval_and_returns_the_local_plan() -> None:
    """respond 先规划；模型在决定 JSON 里回传的 turn_plan 不被接受。"""
    text = "早餐几点"
    assistant, completions = _assistant(
        [_plan_json(text, ("static_fact", text)), _decision(turn_plan={"status": "ok"})]
    )
    decision = await assistant.respond(
        guest_identifier="g", language=Language.ZH,
        messages=[{"role": "user", "content": text}],
    )
    assert _is_planner(completions.requests[0])
    assert decision.turn_plan is not None and decision.turn_plan.ok
    assert decision.turn_plan.plan.items[0].kind == "static_fact"


@pytest.mark.asyncio
async def test_a_matching_plan_is_reused_and_a_stale_one_is_replanned() -> None:
    """合并阶段计划摘要一致时复用，不再规划；不一致时重新规划。"""
    text = "早餐几点"
    assistant, completions = _assistant([_decision()])
    await assistant.respond(
        guest_identifier="g", language=Language.ZH,
        messages=[{"role": "user", "content": text}],
        turn_plan=plan_for(text, ("static_fact", text)),
    )
    assert not any(_is_planner(request) for request in completions.requests)

    assistant, completions = _assistant([_plan_json(text, ("static_fact", text)), _decision()])
    await assistant.respond(
        guest_identifier="g", language=Language.ZH,
        messages=[{"role": "user", "content": text}],
        turn_plan=plan_for("早餐几点？", ("static_fact", "早餐几点")),
    )
    assert _is_planner(completions.requests[0])


@pytest.mark.asyncio
async def test_main_reply_failure_carries_the_completed_plan() -> None:
    """规划成功、主回复失败时，异常带回本轮计划（V6-R1）。"""
    text = "上次房间空调坏了，现在已经修好了"
    assistant, _ = _assistant(
        [_plan_json(text, ("history_mention", text)), "不是JSON", "还不是JSON"]
    )
    with pytest.raises(AssistantUnavailableError) as raised:
        await assistant.respond(
            guest_identifier="g", language=Language.ZH,
            messages=[{"role": "user", "content": text}],
        )
    assert raised.value.plan_outcome is not None
    assert raised.value.plan_outcome.plan.items[0].kind == "history_mention"


@pytest.mark.asyncio
async def test_plan_only_adds_unforced_tools() -> None:
    """计划的房型推荐项只增加开放工具，不改成强制调用（旧规则未命中时 tool_choice=auto）。"""
    text = "一家四口住哪种合适"
    assistant, completions = _assistant(
        [_plan_json(text, ("catalog_query", text)), _decision()]
    )
    assistant._tool_executor = SimpleNamespace(execute=None)
    await assistant.respond(
        guest_identifier="g", language=Language.ZH,
        messages=[{"role": "user", "content": text}],
    )
    main = completions.requests[1]
    assert [tool["function"]["name"] for tool in main["tools"]] == ["list_properties"]
    assert main["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_selected_evidence_is_sent_verbatim() -> None:
    """主调用选中核验通过的条目时，客人收到审核原文，模型自己的说法不采用。"""
    text = "房间有吹风机吗"
    assistant, completions = _assistant(
        [
            _plan_json(text, ("static_fact", text)),
            _decision(
                reply_text="没有吹风机哦。",
                evidence_selection=[{"item_id": 1, "answer_ids": [9040], "related_ids": []}],
            ),
        ],
        _Knowledge([HAIRDRYER]),
    )
    decision = await assistant.respond(
        guest_identifier="g", language=Language.ZH,
        messages=[{"role": "user", "content": text}],
    )
    assert decision.reply_text == HAIRDRYER.answer
    assert decision.reply_parts[0].evidence[0].source_id == "9040"
    envelope = json.loads(completions.requests[1]["messages"][-1]["content"])
    assert envelope["turn_plan"] == [{"item_id": 1, "question": text}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ([{"item_id": 1, "answer_ids": [], "related_ids": []}], "尚未确认"),
        ([], None),
    ],
)
async def test_empty_or_missing_selection(selection, expected) -> None:
    """选「无」回未确认；不回传选择时回到现行证据计划（此处无主题，按普通回复）。"""
    text = "房间有吹风机吗"
    assistant, _ = _assistant(
        [
            _plan_json(text, ("static_fact", text)),
            _decision(reply_text="我帮您看看。", evidence_selection=selection),
        ],
        _Knowledge([HAIRDRYER]),
    )
    decision = await assistant.respond(
        guest_identifier="g", language=Language.ZH,
        messages=[{"role": "user", "content": text}],
    )
    if expected is None:
        assert HAIRDRYER.answer not in decision.reply_text
    else:
        assert expected in decision.reply_text


@pytest.mark.asyncio
async def test_named_room_is_retrieved_with_reserved_slots() -> None:
    """问题点名房号时按该房检索并保留专属名额（D8）；房号只作检索目标。"""
    text = "401适合带5岁小孩住吗"
    knowledge = _Knowledge([], rooms={"401": 401})
    raw = json.dumps(
        {"items": [{"id": 1, "kind": "static_fact", "quote": text, "start": 0,
                    "target_room": "401"}]},
        ensure_ascii=False,
    )
    assistant, _ = _assistant([raw, _decision()], knowledge)
    await assistant.respond(
        guest_identifier="g", language=Language.ZH,
        messages=[{"role": "user", "content": text}],
    )
    item_calls = [kwargs for query, kwargs in knowledge.calls if kwargs.get("limit") == 4]
    assert item_calls and item_calls[0]["property_id"] == 401
    assert item_calls[0]["reserved_property_slots"] == 3


def _breakfast(source_id: int, answer: str, **fields) -> KnowledgeSnippet:
    """早餐条目。"""
    return KnowledgeSnippet(
        source_id=source_id, category="餐饮", question="早餐几点？", answer=answer,
        scope="global", **fields,
    )


def test_conflict_check_covers_unselected_same_group_candidates() -> None:
    """模型只选一条，同主题同属性的其他合法候选给出不同钟点：判冲突，回未确认。"""
    first = _breakfast(1, "早餐7:30开始。")
    second = _breakfast(2, "早餐8:00开始。")
    verdict = verify_selected_evidence("早餐几点", [1], [], [first, second])
    assert verdict.status == "missing"
    assert verdict.reason == "conflict_time"


def test_period_policy_wins_a_conflict_and_out_of_range_ids_fall_back() -> None:
    """D6：特殊时期政策与平时政策矛盾时以特殊时期为准；越界编号交回现行证据计划。"""
    from datetime import date

    regular = _breakfast(1, "早餐7:30开始。")
    holiday = _breakfast(
        2, "国庆期间早餐8:00开始。", valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 7)
    )
    verdict = verify_selected_evidence(
        "早餐几点", [1], [], [regular, holiday], target_date=date(2026, 10, 3),
        target_end_date=date(2026, 10, 4),
    )
    assert verdict.status == "grounded"
    assert verdict.parts[0].text == holiday.answer
    crossing = verify_selected_evidence(
        "早餐几点", [2], [], [regular, holiday], target_date=date(2026, 10, 6),
        target_end_date=date(2026, 10, 9),
    )
    assert crossing.status == "invalid"
    assert verify_selected_evidence("早餐几点", [99], [], [regular]).status == "invalid"


def test_entries_outside_the_target_dates_are_not_legal_answers() -> None:
    """门禁实测：9 月 25 日问早餐，模型选了国庆期间条目。有效期不覆盖目标日期即越界，
    交回现行证据计划；冲突分组也不纳入它。"""
    from datetime import date

    regular = _breakfast(1, "早餐7:30开始。")
    holiday = _breakfast(
        2, "国庆期间早餐8:00开始。", valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 7)
    )
    chose_holiday = verify_selected_evidence(
        "早餐几点", [2], [], [regular, holiday], target_date=date(2026, 9, 25)
    )
    assert (chose_holiday.status, chose_holiday.reason) == ("invalid", "outside_validity")
    chose_regular = verify_selected_evidence(
        "早餐几点", [1], [], [regular, holiday], target_date=date(2026, 9, 25)
    )
    assert chose_regular.status == "grounded"
    assert chose_regular.parts[0].text == regular.answer


def test_unasked_attributes_and_passing_mentions_do_not_conflict() -> None:
    """门禁实测：问婴儿床时，早餐条目里顺带写到儿童收费，不能判成费用冲突。

    冲突只查客人问到的钟点或收费，同组只取问法点名同一主题的条目。"""
    crib = KnowledgeSnippet(
        source_id=1, category="儿童", question="有婴儿床吗？",
        answer="可以免费提供一张婴儿床，需提前告知。", scope="global",
    )
    breakfast = KnowledgeSnippet(
        source_id=2, category="餐饮", question="早餐多少钱？",
        answer="早餐每位28元，儿童早餐每位15元。", scope="global",
    )
    verdict = verify_selected_evidence("宝宝一岁多，有婴儿床吗", [1], [], [crib, breakfast])
    assert verdict.status == "grounded"
