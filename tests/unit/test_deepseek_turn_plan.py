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


def test_related_entries_do_not_create_time_conflicts() -> None:
    """候选门禁实测：问安静时段，模型把客厅开放时间标为相关条目，不能判成钟点冲突。"""
    quiet = KnowledgeSnippet(
        source_id=1, category="规则", question="晚上几点以后要保持安静？",
        answer="每天22:00至次日8:00是安静时段。", scope="global",
    )
    lounge = KnowledgeSnippet(
        source_id=2, category="公共区域", question="有公共客厅吗？几点开放？",
        answer="一楼公共客厅9:00至21:00开放。", scope="global",
    )
    verdict = verify_selected_evidence("晚上几点以后要保持安静？", [1], [2], [quiet, lounge])
    assert verdict.status == "grounded"
    assert verdict.parts[0].text == quiet.answer


def _plan_items(text: str, *items: tuple[str, str, dict]) -> str:
    """规划 JSON：每项带可选附加字段。"""
    return json.dumps(
        {
            "items": [
                {"id": index, "kind": kind, "quote": quote, "start": text.find(quote), **extra}
                for index, (kind, quote, extra) in enumerate(items, start=1)
            ]
        },
        ensure_ascii=False,
    )


def _room_entry(room: int, answer: str) -> KnowledgeSnippet:
    """某房间专属的吹风机条目。"""
    return KnowledgeSnippet(
        source_id=room, category="设施", question="房间吹风机在哪里", answer=answer,
        scope="property", property_id=room,
    )


class _RoomKnowledge(_Knowledge):
    """按检索房源过滤房间专属条目，模拟知识服务的房间过滤。"""

    async def retrieve(self, language, query, **kwargs):
        """只返回通用条目与当前房源的专属条目。"""
        self.calls.append((query, kwargs))
        room = kwargs.get("property_id")
        return [
            entry for entry in self.entries
            if entry.scope != "property" or entry.property_id == room
        ]


@pytest.mark.asyncio
async def test_each_item_only_accepts_evidence_for_its_own_room() -> None:
    """B24-R3：一句问两间房、模型交换编号时，不能用另一间房的审核事实回答本房间。"""
    text = "401房间吹风机在哪里，402房间吹风机在哪里"
    knowledge = _RoomKnowledge(
        [_room_entry(401, "401房间的吹风机在抽屉。"), _room_entry(402, "402房间的吹风机在衣柜。")],
        rooms={"401": 401, "402": 402},
    )
    plan = _plan_items(
        text,
        ("static_fact", "401房间吹风机在哪里", {"target_room": "401"}),
        ("static_fact", "402房间吹风机在哪里", {"target_room": "402"}),
    )
    swapped = [
        {"item_id": 1, "answer_ids": [402], "related_ids": []},
        {"item_id": 2, "answer_ids": [401], "related_ids": []},
    ]
    assistant, _ = _assistant([plan, _decision(evidence_selection=swapped)], knowledge)
    decision = await assistant.respond(
        guest_identifier="g", language=Language.ZH, messages=[{"role": "user", "content": text}]
    )
    for part in decision.reply_parts:
        for evidence in part.evidence:
            assert part.question.startswith(str(evidence.property_id))
    assert "401房间吹风机在哪里" not in [
        part.question for part in decision.reply_parts if "衣柜" in part.text
    ]

    correct = [
        {"item_id": 1, "answer_ids": [401], "related_ids": []},
        {"item_id": 2, "answer_ids": [402], "related_ids": []},
    ]
    assistant, _ = _assistant([plan, _decision(evidence_selection=correct)], knowledge)
    decision = await assistant.respond(
        guest_identifier="g", language=Language.ZH, messages=[{"role": "user", "content": text}]
    )
    assert "401房间的吹风机在抽屉。" in decision.reply_text
    assert "402房间的吹风机在衣柜。" in decision.reply_text


@pytest.mark.asyncio
async def test_unknown_named_room_does_not_borrow_the_confirmed_room_facts() -> None:
    """B24-R3：点名的房号映射不到已知房源时，不用已确认住宿房间的专属事实回答。"""
    from homestay_bot.services.context_retention import CustomerModelContext

    text = "909房间吹风机在哪里"
    knowledge = _RoomKnowledge([_room_entry(401, "401房间的吹风机在抽屉。")], rooms={})
    plan = _plan_items(text, ("static_fact", text, {"target_room": "909"}))
    selection = [{"item_id": 1, "answer_ids": [401], "related_ids": []}]
    assistant, _ = _assistant([plan, _decision(evidence_selection=selection)], knowledge)
    decision = await assistant.respond(
        guest_identifier="g", language=Language.ZH,
        messages=[{"role": "user", "content": text}],
        customer_context=CustomerModelContext(
            confirmed_stay={"property_id": 401, "check_in_date": "2026-10-08",
                            "check_out_date": "2026-10-09"}
        ),
    )
    assert "抽屉" not in decision.reply_text


@pytest.mark.asyncio
async def test_static_evidence_only_replaces_its_own_item() -> None:
    """B24-R4：吹风机加景点推荐，审核答案只接管吹风机项，景点推荐按模型逐项回答保留。"""
    text = "房间有吹风机吗，武汉有哪些经典景点推荐"
    plan = _plan_items(
        text, ("static_fact", "房间有吹风机吗", {}), ("external_info", "武汉有哪些经典景点推荐", {})
    )
    assistant, completions = _assistant(
        [
            plan,
            _decision(
                reply_text="每间房都有吹风机。武汉经典景点可以逛黄鹤楼、东湖和省博物馆。",
                evidence_selection=[{"item_id": 1, "answer_ids": [9040], "related_ids": []}],
                item_answers=[{"item_id": 2, "text": "武汉经典景点可以逛黄鹤楼、东湖和省博物馆。"}],
            ),
        ],
        _Knowledge([HAIRDRYER]),
    )
    decision = await assistant.respond(
        guest_identifier="g", language=Language.ZH, messages=[{"role": "user", "content": text}]
    )
    assert HAIRDRYER.answer in decision.reply_text
    assert "黄鹤楼" in decision.reply_text
    assert decision.reply_text.index(HAIRDRYER.answer) < decision.reply_text.index("黄鹤楼")
    envelope = json.loads(completions.requests[1]["messages"][-1]["content"])
    assert {"item_id": 2, "question": "武汉有哪些经典景点推荐"} in envelope["other_items"]


@pytest.mark.asyncio
async def test_unanswered_item_is_not_backfilled_with_the_raw_model_text() -> None:
    """B24-R4：模型没逐项回答的项留空，不把未限定的整段原文补回来（其中可能有本店断言）。"""
    text = "房间有吹风机吗，武汉有哪些经典景点推荐"
    plan = _plan_items(
        text, ("static_fact", "房间有吹风机吗", {}), ("external_info", "武汉有哪些经典景点推荐", {})
    )
    assistant, _ = _assistant(
        [
            plan,
            _decision(
                reply_text="我们民宿还有免费健身房。黄鹤楼很好玩。",
                evidence_selection=[{"item_id": 1, "answer_ids": [9040], "related_ids": []}],
            ),
        ],
        _Knowledge([HAIRDRYER]),
    )
    decision = await assistant.respond(
        guest_identifier="g", language=Language.ZH, messages=[{"role": "user", "content": text}]
    )
    assert decision.reply_text == HAIRDRYER.answer


@pytest.mark.asyncio
async def test_service_only_plan_is_not_replaced_by_an_unconfirmed_topic_reply() -> None:
    """B24-R4 / SR-毛巾：计划只有服务申请时，证据计划不按话题回「尚未确认」覆盖整轮。"""
    text = "毛巾不够用了，能再给两条吗"
    plan = _plan_items(text, ("service_request", text, {"subject": "毛巾"}))
    assistant, _ = _assistant(
        [plan, _decision(reply_text="好的，给您登记补两条毛巾。")], _Knowledge([])
    )
    decision = await assistant.respond(
        guest_identifier="g", language=Language.ZH, messages=[{"role": "user", "content": text}]
    )
    assert "尚未确认" not in decision.reply_text


def test_same_question_conflict_cannot_be_hidden_by_a_related_label() -> None:
    """B24-R6：标准问题相同的两条健身房开放时间 8:00/10:00，无论怎样分配标签都不能放行。"""
    first = KnowledgeSnippet(
        source_id=1, category="设施", question="健身房几点开放？", answer="健身房每天8:00开放。",
        scope="global",
    )
    second = KnowledgeSnippet(
        source_id=2, category="设施", question="健身房几点开放？",
        answer="健身房每天10:00开放。", scope="global",
    )
    for answers, related in (([1], [2]), ([1], []), ([2], [1]), ([1, 2], [])):
        verdict = verify_selected_evidence("健身房几点开放？", answers, related, [first, second])
        assert verdict.status == "missing", (answers, related)


@pytest.mark.asyncio
async def test_non_action_plan_without_static_items_keeps_the_evidence_plan() -> None:
    """候选门禁 rc-1.69.1-1 实测（K-无障碍-D、MT-停车EN）：规划把本店事实问题判成房型推荐
    或房价时，计划里没有本店事实项，但现行证据计划仍须照常作答；只有全是动作项的计划
    （服务申请、报修等）才不让证据计划接管。"""
    accessible = KnowledgeSnippet(
        source_id=9020, category="无障碍", question="有无障碍房间吗？坐轮椅方便吗？",
        answer="本店没有无障碍客房，入口有2级台阶，没有坡道。", scope="global",
    )
    text = "有无障碍房间吗？"
    plan = _plan_items(text, ("catalog_query", "有无障碍房间吗", {}))
    assistant, _ = _assistant(
        [plan, _decision(reply_text="我们有无障碍客房。")], _Knowledge([accessible])
    )
    assistant._tool_executor = SimpleNamespace(execute=None)
    decision = await assistant.respond(
        guest_identifier="g", language=Language.ZH, messages=[{"role": "user", "content": text}]
    )
    assert "本店没有无障碍客房" in decision.reply_text
    assert "我们有无障碍客房" not in decision.reply_text
