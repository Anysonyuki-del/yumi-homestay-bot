"""房号身份解析（房号身份 Spec v2）：原文写法、房源身份、四种状态与全链共用的快照。"""

from datetime import date

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.services.item_coverage import (
    RoomIdentity,
    clause_needs,
    resolve_rooms,
    room_identity,
    room_mentions,
)
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _availability_row,
    _decision,
    _Knowledge,
    _respond,
    _snippet,
    _tool_message,
    _Tools,
)


@pytest.mark.parametrize("text,expected", [
    ("2303有浴缸吗", [(None, "2303")]),
    ("2303房间几点退房", [(None, "2303")]),
    ("2栋2303有浴缸吗", [("2", "2303")]),
    ("2号楼2303停车", [("2", "2303")]),
    ("2-2303还有房吗", [("2", "2303")]),
    ("201号房几点退房", [(None, "201")]),
    ("订单尾号2303能退款吗", []),
    ("手机尾号是1803", []),
    ("密码：8888对吗", []),
    ("订单尾号2303，2303房间有浴缸吗", [(None, "2303")]),
    ("2026-10-12入住302", [(None, "302")]),
    ("我们3个人住2晚", []),
    ("预算1000元有什么房", []),
])
def test_room_mentions_keep_building_and_skip_identifiers(text, expected):
    """楼栋随号码一起识别；订单号、尾号、手机、密码语境的数字不是房号（R2、R5）。"""
    assert [(m.building, m.number) for m in room_mentions(text)] == expected


@pytest.mark.parametrize("text,residue", [
    ("明天2303还有房吗", ""),
    ("明天2栋2303还有房吗", ""),
    ("明天2-2303还有房吗", ""),
    ("明天2号楼2303房间还有房吗", ""),
    ("2栋2303还有房吗，入口在哪", "入口在哪"),
])
def test_room_mention_is_removed_whole_from_residue(text, residue):
    """剩余语义整段删除房号写法，不留下「2栋」被当成新问题；别的子问照常保留（RI-R2）。"""
    assert clause_needs(text).residue == residue


def test_building_word_alone_is_not_removed():
    """没有号码的「几栋楼」不是房号写法，剩余语义保留楼栋问题。"""
    assert "栋" in clause_needs("几栋楼有电梯").residue


@pytest.mark.parametrize("room_number,title,expected", [
    ("2栋2303", "武汉江山《挽江》2栋2303", ("2", "2303")),
    ("2303", "随便什么标题", (None, "2303")),
    (None, "武汉江山《马尔》2栋2203", ("2", "2203")),
    ("", "芸栖·101 庭院大床房", (None, "101")),
    (None, "收藏家套房", None),
    (None, "2栋2203与2栋2303连通", None),
    ("3栋101", "标题写的是2栋999", ("3", "101")),
])
def test_identity_prefers_room_number_then_unique_title(room_number, title, expected):
    """room_number 为准；为空时只取标题里唯一的房号写法，没有或多个时没有身份（R8）。"""
    identity = room_identity(1, room_number, title)
    assert (None if identity is None else (identity.building, identity.number)) == expected


IDENTITIES = [
    RoomIdentity(2303, "2", "2303"),
    RoomIdentity(1803, "1", "1803"),
    RoomIdentity(2803, "2", "1803"),
]


@pytest.mark.parametrize("text,preferred,status,ids", [
    ("2303", None, "resolved", (2303,)),
    ("2栋2303", None, "resolved", (2303,)),
    ("1栋2303", None, "unknown", ()),
    ("1803", None, "ambiguous", (1803, 2803)),
    ("1803", 1803, "resolved", (1803,)),
    ("1803", 2803, "resolved", (2803,)),
    ("2栋1803", 1803, "resolved", (2803,)),
    ("9999", None, "unknown", ()),
    ("2303和2栋1803", None, "multiple", (2303, 2803)),
    ("早餐几点", None, "none", ()),
])
def test_resolution_has_four_distinct_outcomes(text, preferred, status, ids):
    """同号异栋优先本轮房源，否则歧义；写了楼栋只认该栋；对不上为未知（R3、R4）。"""
    resolution = resolve_rooms(room_mentions(text), IDENTITIES, preferred)
    assert (resolution.status, resolution.property_ids) == (status, ids)


BATH_1 = _snippet(7101, "房间有浴缸吗？", "1栋1803没有浴缸，只有淋浴。", category="卫浴",
                  scope="property", property_id=1803)
BATH_2 = _snippet(7102, "房间有浴缸吗？", "2栋1803有独立浴缸。", category="卫浴",
                  scope="property", property_id=2803)
PARK = _snippet(7103, "停车收费吗？", "2303停车3元/小时。", category="停车",
                scope="property", property_id=2303)
ROOMS = {"2栋2303": 2303, "1栋1803": 1803, "2栋1803": 2803, "302": 1302}


async def _static(text: str, fields: dict, entries, *, conversation_property=None):
    """单项静态问答：真实助手链路，合成规划与主模型；可指定本轮会话房源。"""
    from homestay_bot.integrations.deepseek_client import AssistantRequestContext

    assistant, _ = _assistant([_decision()], _Knowledge(list(entries), ROOMS))
    context = (
        AssistantRequestContext(property_id=conversation_property)
        if conversation_property else None
    )
    return await _respond(
        assistant, text, plan_for(text, ("static_fact", text, fields)),
        **({"request_context": context} if context else {}),
    )


def _sources(decision) -> set[str]:
    """最终分项引用的知识来源。"""
    return {
        e.source_id for part in decision.reply_parts for e in part.evidence
        if e.source_kind == "knowledge"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"target_room": "2303"}])
async def test_bare_number_finds_room_with_building_number(fields):
    """room_number 写「2栋2303」、客人只写「2303」：规划填没填都按该房作答（E1、E3）。"""
    decision = await _static("2303停车怎么收费", fields, [PARK])
    assert "3元/小时" in decision.reply_text
    assert _sources(decision) == {"7103"}
    assert not decision.gap_units


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"target_room": "1803"}])
async def test_building_in_text_beats_bare_planner_number(fields):
    """原文写「2栋1803」：规划漏填或只填裸号都只取 2 栋资料，1 栋资料不混入（R6）。"""
    decision = await _static("2栋1803有浴缸吗", fields, [BATH_1, BATH_2])
    assert "独立浴缸" in decision.reply_text and "只有淋浴" not in decision.reply_text
    assert _sources(decision) == {"7102"}


@pytest.mark.asyncio
@pytest.mark.parametrize("conversation,expected", [(1803, "只有淋浴"), (2803, "独立浴缸")])
async def test_ambiguous_number_uses_conversation_room(conversation, expected):
    """同号两栋时本轮房源在其中就选它（R3）。"""
    decision = await _static("1803有浴缸吗", {}, [BATH_1, BATH_2],
                             conversation_property=conversation)
    assert expected in decision.reply_text


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["1803有浴缸吗", "9999停车怎么收费"])
async def test_unresolved_room_is_unbound_not_referred_or_replaced(text):
    """歧义或未知房号不作答、不转交，也不拿本轮会话房源（2303）的资料顶替（D-R2、D-R3）。"""
    decision = await _static(text, {}, [BATH_1, BATH_2, PARK], conversation_property=2303)
    assert not _sources(decision)
    assert [gap.reason for gap in decision.gap_units] == ["scope_unbound"]


@pytest.mark.asyncio
async def test_planner_hint_cannot_override_the_quote_room():
    """规划错填同轮另一个房号时以本项原文为准（R6）。"""
    text = "302有浴缸吗，2栋1803有浴缸吗"
    quote = "2栋1803有浴缸吗"
    assistant, _ = _assistant([_decision()], _Knowledge([BATH_1, BATH_2], ROOMS))
    decision = await _respond(assistant, text, plan_for(
        text, ("static_fact", quote, {"target_room": "302"}),
    ))
    assert _sources(decision) == {"7102"}


@pytest.mark.asyncio
async def test_two_rooms_in_one_clause_stay_unbound():
    """一个小句点名两个不同房间时不逐房检索，保守不作答（R6 单小句多房号）。"""
    decision = await _static("2栋1803和1栋1803有浴缸吗", {}, [BATH_1, BATH_2])
    assert not _sources(decision)
    assert all(gap.reason == "scope_unbound" for gap in decision.gap_units)


@pytest.mark.asyncio
async def test_planner_room_inherited_by_clause_without_room():
    """本项原文没写房号时，规划房号与全文某处写法完全相等才继承（R6 合法继承、R7）。"""
    text = "我住2栋1803，有浴缸吗"
    quote = "有浴缸吗"
    for hint, expected in [("1803", {"7102"}), ("03", set())]:
        assistant, _ = _assistant([_decision()], _Knowledge([BATH_1, BATH_2], ROOMS))
        decision = await _respond(assistant, text, plan_for(
            text, ("static_fact", quote, {"target_room": hint}),
        ))
        assert _sources(decision) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["明天302还有房吗", "明天2栋2303还有房吗", "明天2-2303还有房吗"])
async def test_tool_answer_has_no_extra_unconfirmed_part(text):
    """规划漏填房号、或写了楼栋：房态照常给出，不再拼一段多余的未确认（E2、E4）。"""
    property_id = 1302 if "302" in text and "2303" not in text else 2303
    args = {"check_in_date": "2026-10-08", "check_out_date": "2026-10-09"}
    tools = _Tools({"search_availability": [_availability_row(property_id, *args.values())]})
    assistant, _ = _assistant(
        [_tool_message("search_availability", args), _decision()],
        _Knowledge([], ROOMS), tools,
    )
    decision = await _respond(assistant, text, plan_for(text, ("stay_query", text, {})))
    assert "可订" in decision.reply_text
    assert "尚未确认" not in decision.reply_text
    assert not decision.gap_units


def test_tool_rows_follow_the_building_not_the_number():
    """同号两栋都返回时只列所问那一栋；无法消歧时两栋都列且各带房名（E5、R3）。"""
    rows = [
        {"property_id": 1803, "property_title": "《丹麦》1栋1803", "stay_available": True,
         "check_in_date": "2026-10-08", "check_out_date": "2026-10-09"},
        {"property_id": 2803, "property_title": "《古家》2栋1803", "stay_available": False,
         "check_in_date": "2026-10-08", "check_out_date": "2026-10-09"},
    ]

    def ids(question: str, preferred: int | None = None) -> list[int]:
        rooms = resolve_rooms(room_mentions(question), IDENTITIES, preferred)
        parts = DeepSeekGuestAssistant._tool_reply_parts(
            "search_availability", {}, rows, Language.ZH, "a", question, rooms,
        )
        return [e.property_id for part in parts for e in part.evidence]

    assert ids("明天2栋1803还有房吗", preferred=1803) == [2803]
    assert ids("明天1803还有房吗", preferred=1803) == [1803]
    assert ids("明天1803还有房吗") == [1803, 2803]


def test_unknown_room_keeps_not_found_header_but_known_room_does_not():
    """对不上任何房源才说「没有找到」；房间存在但工具没返回它时不这么说（D-R3）。"""
    rows = [{"property_id": 1803, "property_title": "《丹麦》1栋1803", "stay_available": True,
             "check_in_date": "2026-10-08", "check_out_date": "2026-10-09"}]
    for question, header in [("明天9999还有房吗", True), ("明天2303还有房吗", False)]:
        rooms = resolve_rooms(room_mentions(question), IDENTITIES, None)
        [part] = DeepSeekGuestAssistant._tool_reply_parts(
            "search_availability", {}, rows, Language.ZH, "a", question, rooms,
        )
        assert part.text.startswith("没有找到「9999」这个房间") is header



# ── 实施审查 IRI-R1 至 IRI-R4 回归 ────────────────────────────────────────────

ARGS = {"check_in_date": "2026-10-08", "check_out_date": "2026-10-09"}
MIXED_ROOMS = {"302": 302, "1栋1803": 1803, "2栋1803": 2803}
MIXED_ROWS = [
    {"property_id": property_id, "property_title": title, "stay_available": True, **ARGS}
    for property_id, title in [(302, "302家庭房"), (1803, "1栋1803"), (2803, "2栋1803")]
]


async def _availability(text, knowledge, rows, *, fallback=False):
    """合成一次房态工具调用后走正常或协议回退出口；规划与工具都是 10 月 8 日入住一晚。"""
    assistant, _ = _assistant(
        [_tool_message("search_availability", ARGS), "invalid" if fallback else _decision()],
        knowledge, _Tools({"search_availability": rows}),
    )
    return await _respond(assistant, text, plan_for(text, ("stay_query", text, dict(ARGS))))


def _evidence_ids(decision) -> list[int]:
    """最终分项引用的房源编号。"""
    return [e.property_id for part in decision.reply_parts for e in part.evidence]


@pytest.mark.asyncio
async def test_explicit_building_never_matches_unknown_building_identity():
    """room_number 只写「1803」的房源不能证明是 2 栋；客人写「2栋1803」时只取 2 栋（IRI-R1）。"""
    from homestay_bot.integrations.deepseek_client import AssistantRequestContext

    entries = [
        _snippet(731, "房间有浴缸吗？", "1栋1803没有浴缸，只有淋浴。", category="卫浴",
                 scope="property", property_id=101),
        _snippet(732, "房间有浴缸吗？", "2栋1803有独立浴缸。", category="卫浴",
                 scope="property", property_id=202),
    ]
    assistant, _ = _assistant([_decision()], _Knowledge(entries, {"1803": 101, "2栋1803": 202}))
    text = "2栋1803有浴缸吗"
    decision = await _respond(
        assistant, text, plan_for(text, ("static_fact", text, {})),
        request_context=AssistantRequestContext(property_id=101),
    )
    assert "独立浴缸" in decision.reply_text and "只有淋浴" not in decision.reply_text
    assert set(_evidence_ids(decision)) == {202}
    only_bare = resolve_rooms(room_mentions("2栋1803"), [RoomIdentity(101, None, "1803")], 101)
    assert only_bare.status == "unknown"


@pytest.mark.parametrize("first,second", [("302", "1803"), ("302", "9999"), ("9999", "1803")])
def test_mixed_resolution_is_order_independent(first, second):
    """已确定、歧义、未知并存时保留全部信息，调换顺序结果相同（IRI-R2）。"""
    identities = [RoomIdentity(302, None, "302"), *IDENTITIES[1:]]
    a = resolve_rooms(room_mentions(f"{first}和{second}"), identities, None)
    b = resolve_rooms(room_mentions(f"{second}和{first}"), identities, None)
    assert (a.status, set(a.property_ids), a.unresolved) == (
        b.status, set(b.property_ids), b.unresolved
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_known_room_survives_ambiguous_neighbour(fallback):
    """「302和1803」：302 与 1803 的两个候选都列出，302 不被歧义吞掉（IRI-R2）。"""
    decision = await _availability(
        "明天302和1803还有房吗", _Knowledge([], MIXED_ROOMS), MIXED_ROWS, fallback=fallback
    )
    assert set(_evidence_ids(decision)) == {302, 1803, 2803}
    assert "尚未确认民宿专属信息" not in decision.reply_text


@pytest.mark.asyncio
async def test_unknown_room_is_named_even_when_another_room_matches():
    """「302和9999」：只列 302，并单独说明没有找到 9999，不接「以下是全部房间」（IRI-R2）。"""
    decision = await _availability("明天302和9999还有房吗", _Knowledge([], MIXED_ROOMS), MIXED_ROWS)
    assert set(_evidence_ids(decision)) == {302}
    assert "没有找到「9999」这个房间。" in decision.reply_text
    assert "以下是全部房间" not in decision.reply_text
    assert "尚未确认民宿专属信息" not in decision.reply_text


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_target_missing_from_tool_does_not_list_other_rooms(fallback):
    """目标 302 已确定但工具没返回它：不把其他房间当结果，只报未查到（IRI-R3）。"""
    decision = await _availability(
        "明天302还有房吗", _Knowledge([], MIXED_ROOMS), MIXED_ROWS[1:], fallback=fallback
    )
    assert _evidence_ids(decision) == []
    assert "1803" not in decision.reply_text
    assert "没有找到" not in decision.reply_text
    assert "暂不能确认房态" in decision.reply_text


def test_cheapest_with_explicit_target_stays_on_target():
    """明确点名房间又问最便宜：只表达所问房间，不进全店最低价（IRI-R3）。"""
    rooms = resolve_rooms(room_mentions("302最便宜多少钱"), [RoomIdentity(302, None, "302")], None)
    rows = [
        {"property_id": 1803, "property_title": "1栋1803", "stay_available": True,
         "nightly_reference_prices": [{"date": "2026-10-08", "price": 100}], **ARGS},
    ]
    parts = DeepSeekGuestAssistant._tool_reply_parts(
        "search_reference_price", ARGS, rows, Language.ZH, "p", "302最便宜多少钱", rooms,
    )
    assert not [e for part in parts for e in part.evidence]


class _BrokenProfiles:
    """房源资料读取失败，知识读取正常。"""

    async def list_room_profiles(self):
        """注入读取故障。"""
        raise RuntimeError("synthetic_read_failure")

    async def list_active(self):
        """没有启用知识。"""
        return []


@pytest.mark.asyncio
async def test_profile_read_failure_is_not_reported_as_missing_room():
    """房源资料读取失败时只说暂时无法核对，不断言房间不存在（IRI-R4）。"""
    from homestay_bot.services.knowledge_service import KnowledgeService

    decision = await _availability(
        "明天302还有房吗", KnowledgeService(_BrokenProfiles()), MIXED_ROWS
    )
    assert "没有找到" not in decision.reply_text
    assert "暂时无法核对「302」对应的房间" in decision.reply_text


@pytest.mark.asyncio
async def test_missing_capability_and_empty_table_are_different():
    """仓储不支持房源资料与成功读到空表不同：前者无法核对，后者没有找到（IRI-R4）。"""
    from homestay_bot.services.knowledge_service import KnowledgeService

    class _NoRooms:
        async def list_active(self):
            return []

    class _EmptyRooms(_NoRooms):
        async def list_room_profiles(self):
            return []

    assert await KnowledgeService(_NoRooms()).room_profiles() is None
    assert await KnowledgeService(_EmptyRooms()).room_profiles() == []
    assert resolve_rooms(room_mentions("302"), None, None).status == "unavailable"
    assert resolve_rooms(room_mentions("302"), [], None).status == "unknown"


# ── 实施复审 IRI-F5、IRI-C1 回归 ─────────────────────────────────────────────

SLASH_ROOMS = {"302": 302, "401": 401, "9999": 9999, "2303": 2303, "2203": 2203}
SLASH_ROWS = [
    {"property_id": property_id, "property_title": f"房{property_id}", "stay_available": True,
     **ARGS}
    for property_id in (302, 401, 9999)
]


@pytest.mark.parametrize("text,rooms", [
    ("明天302/401还有房吗", ["302", "401"]),
    ("明天2303/2203还有房吗", ["2303", "2203"]),
    ("10/08 302有房吗", ["302"]),
    ("2026-10-08 302有房吗", ["302"]),
])
def test_slash_list_is_rooms_not_a_date(text, rooms):
    """「302/401」不能被截成日期「02/40」；真实的「10/08」与 ISO 日期照常识别（IRI-F5）。"""
    from homestay_bot.services.item_coverage import clause_dates

    assert [m.text for m in room_mentions(text)] == rooms
    assert clause_needs(text).residue == ""
    dates = clause_dates(text, 2026)
    assert not dates.unbindable
    assert (dates.check_in is not None) is ("/" not in text or text.startswith("10/"))


def test_invalid_real_date_stays_unbindable():
    """无效的真实日期不会被当成没有日期而继承（IRI-F5 控制）。"""
    from homestay_bot.services.item_coverage import clause_dates

    assert clause_dates("2/30 302有房吗", 2026).unbindable


def test_weekend_with_slash_rooms_keeps_two_stay_options():
    """「周末302/401」不被误当成已有具体日期，仍展开两种周末住法（IRI-F5）。"""
    from datetime import date

    from homestay_bot.integrations.deepseek_client import weekend_stay_options

    slash = weekend_stay_options("周末302/401还有房吗", date(2026, 10, 7))
    joined = weekend_stay_options("周末302和401还有房吗", date(2026, 10, 7))
    assert slash and slash == joined


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("text", ["明天302/401还有房吗", "明天302，401还有房吗",
                                  "明天302,401还有房吗", "明天302、401还有房吗"])
async def test_listed_rooms_only_cite_asked_rooms_without_extra_unconfirmed(text, fallback):
    """斜杠、逗号、顿号并列的房号：只列所问两间，不扩大到 9999，不多拼未确认（IRI-F5、C1）。"""
    decision = await _availability(text, _Knowledge([], SLASH_ROOMS), SLASH_ROWS,
                                   fallback=fallback)
    assert set(_evidence_ids(decision)) == {302, 401}
    assert "尚未确认" not in decision.reply_text
    assert not decision.gap_units


@pytest.mark.asyncio
async def test_independent_questions_after_a_room_are_kept():
    """「302有房吗，401有浴缸吗」：第二句是另一间房的静态问题，仍按未绑定保留（IRI-C1）。"""
    decision = await _availability(
        "明天302有房吗，401有浴缸吗", _Knowledge([], SLASH_ROOMS), SLASH_ROWS
    )
    assert [gap.text for gap in decision.gap_units if gap.reason == "scope_unbound"] == [
        "401有浴缸吗"
    ]


@pytest.mark.asyncio
async def test_room_then_static_question_keeps_the_question():
    """「302，停车怎么收费」：后半句是独立问题，不因房号片段豁免而丢失（IRI-C1 控制）。"""
    entry = _snippet(7201, "停车收费吗？", "302停车每天20元封顶。", category="停车",
                     scope="property", property_id=1302)
    decision = await _static("302，停车怎么收费", {}, [entry])
    assert "20元封顶" in decision.reply_text


# ── 实施复审 IRI-F6 回归：逗号枚举的部分结果 ────────────────────────────────

def _rows(tool: str, property_ids):
    """合成工具行：房态可订，或每晚 200 元的参考价。"""
    rows = []
    for property_id in property_ids:
        row = {"property_id": property_id, "property_title": f"{property_id}测试房",
               "stay_available": True, **ARGS}
        if tool == "search_reference_price":
            row["nightly_reference_prices"] = [{"date": "2026-10-08", "price": 200}]
        rows.append(row)
    return rows


async def _tool_turn(text, tool, rows, *, fallback=False):
    """合成一次指定工具调用后走正常或协议回退出口；规划与工具都是 10 月 8 日入住一晚。"""
    assistant, _ = _assistant(
        [_tool_message(tool, ARGS), "invalid" if fallback else _decision()],
        _Knowledge([], SLASH_ROOMS), _Tools({tool: rows}),
    )
    return await _respond(assistant, text, plan_for(text, ("stay_query", text, dict(ARGS))))


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("comma", ["，", ","])
@pytest.mark.parametrize("tool,ask", [("search_availability", "还有房吗"),
                                      ("search_reference_price", "多少钱")])
@pytest.mark.parametrize("returned", [(401,), (302,)])
async def test_comma_listed_room_missing_from_tool_is_reported(fallback, comma, tool, ask,
                                                               returned):
    """「明天302，401还有房吗」工具只返回一间：另一间必须报未查到，不能静默漏答（IRI-F6）。"""
    decision = await _tool_turn(f"明天302{comma}401{ask}", tool, _rows(tool, returned),
                                fallback=fallback)
    assert set(_evidence_ids(decision)) == set(returned)
    assert [gap.reason for gap in decision.gap_units] == ["tool_uncovered"]
    assert "尚未确认民宿专属信息" not in decision.reply_text


@pytest.mark.asyncio
async def test_comma_listed_unknown_room_is_not_skipped():
    """枚举里的未知房号单独说明「没有找到」，已知房间照常列出（IRI-F6）。"""
    decision = await _tool_turn("明天8888，401还有房吗",
                                "search_availability", _rows("search_availability", (401,)))
    assert set(_evidence_ids(decision)) == {401}
    assert "没有找到「8888」这个房间。" in decision.reply_text
    assert [gap.reason for gap in decision.gap_units] == ["tool_uncovered"]


@pytest.mark.asyncio
async def test_enumeration_does_not_swallow_an_independent_target():
    """「302，401有房吗，501多少钱」：501 的房价是独立目标，不并入前面的房态枚举（IRI-F6）。"""
    from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant as Assistant
    from homestay_bot.services.item_coverage import clause_needs, split_clauses

    text = "明天302，401有房吗，501多少钱"
    spans = split_clauses(text)
    identities = [RoomIdentity(i, None, str(i)) for i in (302, 401, 501)]
    rooms, delegated, _, _ = Assistant._clause_rooms(
        text, spans, [clause_needs(c.text) for c in spans], identities, None,
        date(2026, 10, 7), date(2026, 10, 8),
    )
    assert delegated == (True, False, False)
    assert [r.property_ids for r in rooms[1:]] == [(302, 401), (501,)]


def test_fragment_with_explicit_date_is_not_merged():
    """片段带明确日期时不并入下一句，避免把它的日期套到另一段（IRI-F6 控制）。"""
    from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant as Assistant
    from homestay_bot.services.item_coverage import clause_needs, split_clauses

    text = "10月8日302，401还有房吗"
    spans = split_clauses(text)
    identities = [RoomIdentity(i, None, str(i)) for i in (302, 401)]
    _, delegated, _, orphans = Assistant._clause_rooms(
        text, spans, [clause_needs(c.text) for c in spans], identities, None,
        date(2026, 10, 7), date(2026, 10, 8),
    )
    assert delegated == (False, False)
    assert orphans == (True, False), "没能并入的片段要保留保守提示"


# ── 实施复审 IRI-F7、F6 展示、C3 回归 ─────────────────────────────────────────

FAR = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-11"}


async def _far_turn(text, tool, *, fallback=False):
    """规划与工具都是 10 月 10 日入住一晚（今日 10 月 7 日），302、401 都返回。"""
    rows = [dict(row, **FAR) for row in _rows(tool, (302, 401))]
    assistant, _ = _assistant(
        [_tool_message(tool, FAR), "invalid" if fallback else _decision()],
        _Knowledge([], SLASH_ROOMS), _Tools({tool: rows}),
    )
    return await _respond(assistant, text, plan_for(text, ("stay_query", text, dict(FAR))))


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("tool,ask", [("search_availability", "还有房吗"),
                                      ("search_reference_price", "多少钱")])
@pytest.mark.parametrize("fragment", ["明天302", "第二天302"])
async def test_fragment_date_different_from_receiver_is_not_merged(fragment, tool, ask,
                                                                   fallback):
    """「明天302，10月10日401…」：302 问的是 10 月 8 日，不能按 10 月 10 日宣布已覆盖（IRI-F7）。"""
    decision = await _far_turn(f"{fragment}，10月10日401{ask}", tool, fallback=fallback)
    assert decision.gap_units, "302 的日期没有被核对，不能当作已全部覆盖"


@pytest.mark.asyncio
async def test_fragment_with_same_day_is_merged():
    """片段与接收问法是同一天（明天 = 10 月 8 日）时照常并入，没有多余提示（IRI-F7 正向控制）。"""
    decision = await _availability(
        "明天302，10月8日401还有房吗", _Knowledge([], SLASH_ROOMS), SLASH_ROWS[:2]
    )
    assert set(_evidence_ids(decision)) == {302, 401}
    assert not decision.gap_units and "尚未确认" not in decision.reply_text


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_uncovered_notice_names_the_whole_group(fallback):
    """只返回 401 时，未查到说明引用整组原文，不说「401还有房吗没查到」（IRI-F6 展示）。"""
    decision = await _tool_turn("明天302，401还有房吗", "search_availability",
                                _rows("search_availability", (401,)), fallback=fallback)
    assert "「明天302，401还有房吗」这次没有查到" in decision.reply_text
    assert "「401还有房吗」" not in decision.reply_text
    assert [gap.text for gap in decision.gap_units] == ["明天302，401还有房吗"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("text", ["明天302，有房吗", "明天302，302还有房吗"])
async def test_handed_fragment_is_not_queried_as_a_static_question(text, fallback):
    """单一房源时已交接的片段不再当静态问题取证，不多拼未确认（IRI-C3）。"""
    decision = await _tool_turn(text, "search_availability",
                                _rows("search_availability", (302,)), fallback=fallback)
    assert set(_evidence_ids(decision)) == {302}
    assert "尚未确认" not in decision.reply_text
    assert not decision.gap_units


# ── 1.70.2 候选 all 门禁 AV-102 回归：订房意图不是资料问题 ─────────────────────

@pytest.mark.parametrize("text,residue", [
    ("我想订102", ""), ("今晚和明晚两晚", ""), ("想预订302", ""), ("订一间302", ""),
    ("怎么预订", "怎么预订"), ("预订要交定金吗", "预订交定金"), ("能订早餐吗", "早餐"),
])
def test_booking_intent_alone_leaves_no_residue(text, residue):
    """只有订房意图词与连接词时没有剩余；真的在问订房规则或别的事时保留（AV-102）。"""
    assert clause_needs(text).residue == residue


@pytest.mark.parametrize("fields", [
    {}, {"target_room": "102"},
    {"target_room": "102", "check_in_date": "2026-10-07", "check_out_date": "2026-10-09"},
])
def test_booking_request_offers_no_knowledge_gap(fields):
    """「我想订102，今晚和明晚两晚」不向模型开放资料缺口，避免它先介绍房间而不查房态。"""
    import asyncio

    from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant as Assistant

    text = "我想订102，今晚和明晚两晚"
    assistant, _ = _assistant([_decision()], _Knowledge([], {"102": 1102}))
    plan = plan_for(text, ("stay_query", text, fields))
    assert plan.plan is not None
    layout = asyncio.run(assistant._fact_layout(
        plan.plan.items, text, None, (None, None), date(2026, 10, 7),
        [RoomIdentity(1102, None, "102")],
    ))
    assert Assistant._gap_unit_offers(layout) == []
