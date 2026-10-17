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
    """「302有房吗，401有浴缸吗」：第二句是另一间房的独立静态问题，必须保留（IRI-C1）。

    1.70.4 起同项点名多个房间时按自己的房间单独取证（房号省略追问 Spec D-T2）：401 确实
    没有浴缸资料，记为 401 的资料缺失，可按现有核实流程转交（D-T5），不再只是未绑定。
    """
    decision = await _availability(
        "明天302有房吗，401有浴缸吗", _Knowledge([], SLASH_ROOMS), SLASH_ROWS
    )
    assert [(gap.text, gap.reason, gap.property_id) for gap in decision.gap_units] == [
        ("401有浴缸吗", "knowledge_missing", 401)
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
    assert orphans == ("target", None), "没并入的片段仍是工具目标，按证据核对"


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


# ── 发布复审 R172-1 回归：未接管的房号片段不能静默丢掉 ─────────────────────────

ROOMS_3 = {str(n): n for n in (302, 401, 501)}


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("tool,ask", [("search_availability", "还有房吗"),
                                      ("search_reference_price", "多少钱")])
async def test_trailing_room_follow_up_keeps_a_gap(tool, ask, fallback):
    """「明天401还有房吗，302呢」工具只返回 401：302 必须有保守缺口（E172-1）。"""
    assistant, _ = _assistant(
        [_tool_message(tool, ARGS), "invalid" if fallback else _decision()],
        _Knowledge([], ROOMS_3), _Tools({tool: _rows(tool, (401,))}),
    )
    text = f"明天401{ask}，302呢"
    decision = await _respond(assistant, text, plan_for(text, ("stay_query", text, dict(ARGS))))
    assert ("302呢", "tool_uncovered") in [(g.text, g.reason) for g in decision.gap_units]
    assert all(g.reason != "knowledge_missing" for g in decision.gap_units)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("middle", ["第二天401", "10月9日401"])
async def test_interrupted_enumeration_keeps_earlier_fragments(middle, fallback):
    """「明天302，第二天401，501还有房吗」：302 与 401 都留下缺口，不强行同日合并（E172-2）。"""
    assistant, _ = _assistant(
        [_tool_message("search_availability", ARGS), "invalid" if fallback else _decision()],
        _Knowledge([], ROOMS_3),
        _Tools({"search_availability": _rows("search_availability", (501,))}),
    )
    text = f"明天302，{middle}，501还有房吗"
    decision = await _respond(assistant, text, plan_for(text, ("stay_query", text, dict(ARGS))))
    gaps = {g.text: g.reason for g in decision.gap_units}
    assert gaps.get("明天302") == "tool_uncovered"
    # 「第二天」无锚点、「10月9日」与入住日 10 月 8 日不同：都证明不了日期，记未绑定。
    assert gaps.get(middle) == "scope_unbound"
    assert "knowledge_missing" not in gaps.values()


def test_booking_intent_fragment_is_still_not_an_orphan():
    """「我想订102，今晚和明晚两晚」：不并入，作为工具目标交给覆盖判定，不开放资料缺口（AV-102）。"""
    from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant as Assistant
    from homestay_bot.services.item_coverage import split_clauses

    text = "我想订102，今晚和明晚两晚"
    spans = split_clauses(text)
    _, delegated, _, orphans = Assistant._clause_rooms(
        text, spans, [clause_needs(c.text) for c in spans],
        [RoomIdentity(1102, None, "102")], None, date(2026, 10, 7), None,
    )
    assert delegated == (False, False) and orphans == ("target", None)



# ── R172-F1/F2 回归：房号片段是工具目标，不进静态取证 ───────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("verb", ["订", "住", "要"])
async def test_room_request_synonyms_have_no_fake_gap(verb, fallback):
    """「我想订/住/要302，今晚和明晚两晚」房态完整返回：无资料缺口、无多余未确认（R172-F1）。"""
    stay = {"check_in_date": "2026-10-07", "check_out_date": "2026-10-09"}
    row = {"property_id": 302, "property_title": "302房间", "stay_available": True, **stay}
    assistant, _ = _assistant(
        [_tool_message("search_availability", stay), "invalid" if fallback else _decision()],
        _Knowledge([], ROOMS_3), _Tools({"search_availability": [row]}),
    )
    text = f"我想{verb}302，今晚和明晚两晚"
    decision = await _respond(assistant, text, plan_for(text, ("stay_query", text, stay)))
    assert "尚未确认" not in decision.reply_text
    assert not decision.gap_units


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("hint", [None, "401", "302"])
async def test_planner_room_hint_cannot_turn_follow_up_into_referral(hint, fallback):
    """规划房号缺失、填 401 或填 302：「302呢」的缺口性质一致、不可转交；
    模型主动选中它的编号也不能生成转交候选（R172-F2）。"""
    fields = {**ARGS, **({"target_room": hint} if hint else {})}
    text = "明天401还有房吗，302呢"
    plan = plan_for(text, ("stay_query", text, fields))
    knowledge = _Knowledge([], ROOMS_3)
    probe, _ = _assistant([_decision()], knowledge)
    layout = await probe._fact_layout(
        plan.plan.items, text, None, (None, None), date(2026, 10, 7),
        [RoomIdentity(n, None, str(n)) for n in (302, 401, 501)],
    )
    from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant as Assistant

    assert Assistant._gap_unit_offers(layout) == []
    referral = [{"unit_id": "1:10-14", "canonical_question": "302房间介绍", "category": "房间设施"}]
    assistant, _ = _assistant(
        [_tool_message("search_availability", ARGS),
         "invalid" if fallback else _decision(gap_referrals=referral)],
        knowledge, _Tools({"search_availability": _rows("search_availability", (401,))}),
    )
    decision = await _respond(assistant, text, plan)
    assert [(g.text, g.reason) for g in decision.gap_units] == [("302呢", "tool_uncovered")]
    assert not decision.referral_candidates


@pytest.mark.asyncio
async def test_follow_up_covered_by_tool_has_no_notice():
    """「302呢」工具也返回了 302：有证据就不提示（R172 实际证据维度）。"""
    assistant, _ = _assistant(
        [_tool_message("search_availability", ARGS), _decision()],
        _Knowledge([], ROOMS_3),
        _Tools({"search_availability": _rows("search_availability", (401, 302))}),
    )
    text = "明天401还有房吗，302呢"
    decision = await _respond(assistant, text, plan_for(text, ("stay_query", text, dict(ARGS))))
    assert set(_evidence_ids(decision)) == {401, 302}
    assert not decision.gap_units and "尚未确认" not in decision.reply_text


# ── R172-G1/G2 回归：证据只取消它真正覆盖的目标 ─────────────────────────────────

STAY_8_9 = {"check_in_date": "2026-10-08", "check_out_date": "2026-10-09"}


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("tool,ask", [("search_availability", "还有房吗"),
                                      ("search_reference_price", "多少钱")])
@pytest.mark.parametrize("fragment,covered", [
    ("10月8日至10日302", False), ("10月8日至9日302", True), ("10月9日退房302", False),
])
async def test_fragment_range_needs_both_endpoints(fragment, covered, tool, ask, fallback):
    """片段写了区间时入住、退房两端都要与规划一致；只写退房日证明不了（R172-G1）。"""
    rows = [dict(row, **STAY_8_9) for row in _rows(tool, (302, 401))]
    assistant, _ = _assistant(
        [_tool_message(tool, STAY_8_9), "invalid" if fallback else _decision()],
        _Knowledge([], ROOMS_3), _Tools({tool: rows}),
    )
    text = f"{fragment}，401{ask}"
    decision = await _respond(assistant, text, plan_for(text, ("stay_query", text, STAY_8_9)))
    gaps = [(g.text, g.reason) for g in decision.gap_units]
    assert gaps == ([] if covered else [(fragment, "scope_unbound")])
    assert all(reason != "knowledge_missing" for _, reason in gaps)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("text,rows,missing", [
    ("401有什么房型，302呢", (401,), "302呢"),
    ("401有什么房型，9999呢", (401,), "9999呢"),
    ("401有什么房型，302呢", (401, 302), None),
    ("有什么房型", (401,), None),
])
async def test_catalog_named_room_needs_its_own_row(text, rows, missing, fallback):
    """目录点名房间时逐个房源核对，别的房间的行不能替它作答；全店目录不变（R172-G2）。"""
    catalog = [{"id": n, "title": f"{n}房间"} for n in rows]
    assistant, _ = _assistant(
        [_tool_message("list_properties", {}), "invalid" if fallback else _decision()],
        _Knowledge([], {"302": 302, "401": 401}), _Tools({"list_properties": catalog}),
    )
    decision = await _respond(assistant, text, plan_for(text, ("catalog_query", text, {})))
    gaps = [(g.text, g.reason) for g in decision.gap_units]
    if missing is None:
        assert gaps == []
    else:
        assert gaps == [(missing, "tool_uncovered")]
        assert f"「{missing}」这次没有查到对应的房源资料" in decision.reply_text
    assert not decision.referral_candidates


# ── 1.70.4 句内房号省略追问承接（房号追问 Spec v4.1 §2.6）─────────────────────

from homestay_bot.services.item_coverage import is_room_follow_up  # noqa: E402

FU_ROOMS = {"302": 302, "401": 401, "501": 501, "1栋1803": 1803, "2栋1803": 2803}
FU_ENTRIES = [
    _snippet(8401, "早餐几点开始？", "401早餐8:00开始。", category="早餐",
             scope="property", property_id=401),
    _snippet(8302, "早餐几点开始？", "302早餐9:00开始。", category="早餐",
             scope="property", property_id=302),
    _snippet(8501, "早餐几点开始？", "501早餐7:30开始。", category="早餐",
             scope="property", property_id=501),
]
FU_TEXT = "401早餐几点，302呢"
FU_DATED = "明天401早餐几点，302呢"
DATE_ASK = "入住"


async def _follow_up(text, plan, *, fallback=False, tool=None, entries=FU_ENTRIES,
                     knowledge=None, decision=None, rows=None):
    """静态追问的真实助手链路：可注入主模型硬调用工具，再走正常或协议回退出口。"""
    responses = [_tool_message(tool, ARGS)] if tool else []
    responses.append("invalid" if fallback else (decision or _decision()))
    tools = _Tools({name: rows if rows is not None else _rows(name, (302, 401))
                    for name in ("search_availability", "search_reference_price",
                                 "list_properties")})
    assistant, _ = _assistant(responses, knowledge or _Knowledge(list(entries), FU_ROOMS), tools)
    return await _respond(assistant, text, plan)


def _gaps(decision):
    """缺口的（原文、原因、房源）三元组。"""
    return [(g.text, g.reason, g.property_id) for g in decision.gap_units]


@pytest.mark.parametrize("text,expected", [
    ("302呢", True), ("那302呢", True), ("那么302呢", True), ("302的呢", True),
    ("302也是吗", True), ("302也一样吗", True), ("302也这样吗", True),
    ("那2栋302房间呢", True), ("302呢？", True),
    ("我想订302", False), ("我想住302", False), ("我要302", False), ("302", False),
    ("302怎么样", False), ("10月10日302呢", False), ("明天302呢", False),
    ("第二天302呢", False), ("13月40日302呢", False), ("早餐呢", False),
])
def test_follow_up_shape_is_a_closed_set_without_dates(text, expected):
    """省略句式是有限集合；任何日期写法（含无法绑定的）都让它退回现有流程（§2.1、C4-1）。"""
    assert is_room_follow_up(text) is expected


FU_PLANS = {
    "P1": lambda t: plan_for(t, ("static_fact", "401早餐几点", {"target_room": "401"}),
                             ("static_fact", "302呢",
                              {"target_room": "302", "question": "302早餐几点开始？"})),
    "P2": lambda t: plan_for(t, ("static_fact", "401早餐几点", {"target_room": "401"}),
                             ("static_fact", "302呢", {"target_room": "302"})),
    "P3": lambda t: plan_for(t, ("static_fact", t, {"target_room": "401"})),
    "P4": lambda t: plan_for(t, ("static_fact", t, {})),
    "P5": lambda t: plan_for(t, ("stay_query", t, {"target_room": "401"})),
    "catalog": lambda t: plan_for(t, ("catalog_query", t, {})),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(FU_PLANS))
async def test_follow_up_inherits_static_topic_under_every_plan(name):
    """「401早餐几点，302呢」无论规划拆分、合并还是误标为住宿或目录：两间各答自己的早餐，
    按原文顺序，不追问入住日期、不留缺口（P1 至 P5）。"""
    decision = await _follow_up(FU_TEXT, FU_PLANS[name](FU_TEXT))
    text = decision.reply_text
    assert "8:00" in text and "9:00" in text and "7:30" not in text
    assert text.index("8:00") < text.index("9:00")
    assert not decision.gap_units and DATE_ASK not in text
    assert _sources(decision) == {"8401", "8302"} or _sources(decision) == {8401, 8302}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [None, "search_availability", "search_reference_price"])
async def test_dated_stay_label_with_forced_tool_still_answers_breakfast(tool):
    """P6「明天401早餐几点，302呢」规划误标为带日期的住宿项，主模型还硬调用工具：日期不证明
    住宿意图（D-T3），这一项不开放工具，硬调用被拒后重试，最终只有两间早餐（§2.7）。"""
    plan = plan_for(FU_DATED, ("stay_query", FU_DATED, {"target_room": "401", **ARGS}))
    decision = await _follow_up(FU_DATED, plan, tool=tool)
    text = decision.reply_text
    assert "8:00" in text and "9:00" in text
    assert "可订" not in text and "200" not in text and not decision.gap_units


@pytest.mark.asyncio
async def test_unopened_tool_then_invalid_output_is_existing_unavailable_path():
    """对照：主模型调用了未开放的工具、随后输出不合法时整轮不可用，这是既有协议行为，
    不带房号追问的纯静态问题同样如此，不是本功能引入的。

    「工具后协议回退」只在本轮真的开放并执行了工具时存在；纯静态单元不开放工具（§2.7），
    所以本节只有带真实工具需求的用例才覆盖回退出口。
    """
    from homestay_bot.integrations.deepseek_client import AssistantUnavailableError

    for text in (FU_DATED, "明天早餐几点"):
        plan = plan_for(text, ("static_fact", text, {}))
        with pytest.raises(AssistantUnavailableError):
            await _follow_up(text, plan, fallback=True, tool="search_availability")


@pytest.mark.asyncio
async def test_chained_follow_up_uses_previous_normalized_question():
    """连续追问「401早餐几点，302呢，501呢」：501 承接 302 已规范化的早餐问法。"""
    text = "401早餐几点，302呢，501呢"
    decision = await _follow_up(text, plan_for(text, ("static_fact", text, {})))
    reply = decision.reply_text
    assert reply.index("8:00") < reply.index("9:00") < reply.index("7:30")
    assert not decision.gap_units


@pytest.mark.asyncio
async def test_wrong_room_selection_is_intersected_with_unit_candidates():
    """同项两间房答案不同，模型故意把 401 的条目选给整项：302 只能用自己的候选（T-R4）。"""
    selection = [{"item_id": 1, "answer_ids": [8401], "related_ids": []}]
    decision = await _follow_up(
        FU_TEXT, FU_PLANS["P4"](FU_TEXT),
        decision=_decision(evidence_selection=selection),
    )
    assert "9:00" in decision.reply_text and decision.reply_text.count("8:00") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("swap", [False, True])
async def test_overlapping_items_are_owned_by_text_position_not_array_order(swap):
    """整句项与「302呢」项重叠：归属按起点、覆盖长度和编号决定，与数组顺序无关。"""
    items = [("static_fact", FU_TEXT, {"target_room": "401"}),
             ("static_fact", "302呢", {"target_room": "302"})]
    plan = plan_for(FU_TEXT, *(reversed(items) if swap else items))
    decision = await _follow_up(FU_TEXT, plan)
    assert decision.reply_text.count("8:00") == 1 and decision.reply_text.count("9:00") == 1
    assert not decision.gap_units and "尚未确认" not in decision.reply_text


@pytest.mark.asyncio
async def test_each_group_keeps_its_own_dates():
    """两组不同日期的追问各按本组日期取证：302 只在 10 月 8 日有资料，501 只在 10 月 10 日有。"""
    entries = [
        _snippet(8402, "早餐几点开始？", "401早餐8:00开始。", category="早餐",
                 scope="property", property_id=401),
        _snippet(8303, "早餐几点开始？", "302早餐9:00开始。", category="早餐",
                 scope="property", property_id=302,
                 valid_from=date(2026, 10, 8), valid_until=date(2026, 10, 8)),
        _snippet(8502, "早餐几点开始？", "501早餐7:30开始。", category="早餐",
                 scope="property", property_id=501,
                 valid_from=date(2026, 10, 10), valid_until=date(2026, 10, 10)),
    ]
    text = "10月8日401早餐几点，302呢；10月10日401早餐几点，501呢"
    first, second = text.split("；")
    plan = plan_for(text, ("static_fact", first, dict(ARGS)), ("static_fact", second, {
        "check_in_date": "2026-10-10", "check_out_date": "2026-10-11"}))
    decision = await _follow_up(text, plan, entries=entries)
    assert "9:00" in decision.reply_text and "7:30" in decision.reply_text
    assert not [g for g in decision.gap_units if g.property_id in (302, 501)]


@pytest.mark.asyncio
async def test_follow_up_cannot_borrow_parent_date_for_a_different_day():
    """302 只在 10 月 10 日有资料，父句问的是 10 月 8 日：追问不能借别的日子确认。"""
    entries = [FU_ENTRIES[0], _snippet(
        8304, "早餐几点开始？", "302早餐9:00开始。", category="早餐", scope="property",
        property_id=302, valid_from=date(2026, 10, 10), valid_until=date(2026, 10, 10))]
    text = "10月8日401早餐几点，302呢"
    decision = await _follow_up(text, plan_for(text, ("static_fact", text, dict(ARGS))),
                                entries=entries)
    assert "9:00" not in decision.reply_text
    assert [(g.reason, g.property_id) for g in decision.gap_units] == [
        ("knowledge_missing", 302)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["static_fact", "stay_query", "catalog_query"])
@pytest.mark.parametrize("text,fragment", [
    ("401早餐几点，999呢", "999呢"),
    ("302呢", "302呢"),
    ("401早餐几点\n302呢", "302呢"),
    ("401早餐几点，1803呢", "1803呢"),
    ("帮我开张发票，302呢", "302呢"),
])
async def test_unbound_follow_up_is_scope_unbound_without_date_ask(text, fragment, kind):
    """D 类（V3-R1）：没有父句、隔着换行、未知或歧义房号、父句是动作请求，三种规划写法与两个
    出口都只记 scope_unbound，不追问入住日期、不展示工具结果、不生成转交候选。"""
    if text.startswith("帮我"):
        # 动作请求在真实规划里是服务事项；被误标成静态事实时本地无法区分，属已知上限。
        plan, item_id = plan_for(text, ("service_request", "帮我开张发票"), (kind, fragment)), 2
    else:
        plan, item_id = plan_for(text, (kind, text, {})), 1
    unit = f"{item_id}:{text.index(fragment)}-{text.index(fragment) + len(fragment)}"
    referral = [{"unit_id": unit, "canonical_question": "302早餐几点", "category": "早餐"}]
    decision = await _follow_up(text, plan,
                                decision=_decision(gap_referrals=referral))
    assert (fragment, "scope_unbound") in [(g.text, g.reason) for g in decision.gap_units]
    assert "9:00" not in decision.reply_text and "可订" not in decision.reply_text
    assert DATE_ASK not in decision.reply_text and not decision.referral_candidates


@pytest.mark.asyncio
async def test_follow_up_with_profile_read_failure_is_unbound():
    """房源资料读取失败时追问无法绑定房源：只记 scope_unbound，不按全店资料作答。"""
    from homestay_bot.services.knowledge_service import KnowledgeService

    decision = await _follow_up(FU_TEXT, FU_PLANS["P4"](FU_TEXT),
                                knowledge=KnowledgeService(_BrokenProfiles()))
    assert ("302呢", "scope_unbound") in [(g.text, g.reason) for g in decision.gap_units]
    assert DATE_ASK not in decision.reply_text


@pytest.mark.asyncio
@pytest.mark.parametrize("booking", ["我想订302", "我想住302", "我要302"])
async def test_booking_after_static_question_keeps_existing_stay_flow(booking):
    """A 类对照：「401早餐几点，我想订302」不被吞成早餐问题，302 仍走既有住宿流程。"""
    text = f"401早餐几点，{booking}"
    plan = plan_for(text, ("static_fact", "401早餐几点", {"target_room": "401"}),
                    ("stay_query", booking, {"target_room": "302"}))
    decision = await _follow_up(text, plan)
    assert "8:00" in decision.reply_text and "9:00" not in decision.reply_text
    assert not [g for g in decision.gap_units if g.reason == "knowledge_missing"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", ["302也是吗", "302也一样吗", "302也这样吗", "那302的呢"])
async def test_other_follow_up_shapes_bind_the_same_topic(tail):
    """「302也是吗」等写法的剩余不为空，但与「302呢」得到同一话题和房源（C4-1）。"""
    text = f"401早餐几点，{tail}"
    decision = await _follow_up(text, plan_for(text, ("static_fact", text, {})))
    assert "8:00" in decision.reply_text and "9:00" in decision.reply_text
    assert not decision.gap_units


@pytest.mark.asyncio
async def test_inherited_missing_knowledge_is_referred_with_dated_question():
    """成功继承、302 真缺资料：可转交（D-T5），卡片问法是带日期的规范问题而不是「302呢」。"""
    plan = plan_for(FU_DATED, ("static_fact", FU_DATED, {}))
    first = await _follow_up(FU_DATED, plan, entries=[FU_ENTRIES[0]])
    [gap] = first.gap_units
    assert (gap.reason, gap.property_id) == ("knowledge_missing", 302)
    assert gap.text == "2026-10-08：302早餐几点"
    referral = [{"unit_id": gap.unit_id, "canonical_question": "302早餐几点开始",
                 "category": "早餐"}]
    decision = await _follow_up(FU_DATED, plan, entries=[FU_ENTRIES[0]],
                                decision=_decision(gap_referrals=referral))
    assert [c.unit.text for c in decision.referral_candidates] == [gap.text]


@pytest.mark.asyncio
async def test_unit_retrieval_over_budget_is_not_referable():
    """四个计划项已用完检索预算：再承接的追问记 budget，不可转交，也不被静默吞掉。"""
    text = "401早餐几点，302呢，停车收费吗，wifi密码多少，几点退房"
    plan = plan_for(text, ("static_fact", "401早餐几点，302呢", {}),
                    ("static_fact", "停车收费吗", {}), ("static_fact", "wifi密码多少", {}),
                    ("static_fact", "几点退房", {}))
    decision = await _follow_up(text, plan)
    assert ("302呢", "budget") in [(g.text, g.reason) for g in decision.gap_units] or any(
        g.reason == "budget" and "302" in g.text for g in decision.gap_units
    )
    assert not decision.referral_candidates


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_whole_store_tool_need_is_not_narrowed_by_static_follow_up(fallback):
    """「明天还有房吗，302早餐几点，401呢」：全店房态照常展示，302、401 只答早餐（V3-R2）。"""
    text = "明天还有房吗，302早餐几点，401呢"
    plan = plan_for(text, ("stay_query", text, dict(ARGS)))
    decision = await _follow_up(text, plan, fallback=fallback, tool="search_availability",
                                rows=_rows("search_availability", (302, 401, 9999)))
    reply = decision.reply_text
    assert "9999测试房" in reply and "9:00" in reply and "8:00" in reply


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_named_tool_target_ignores_static_follow_up_rooms(fallback):
    """「明天401还有房吗，302早餐几点，501呢」：工具只按 401 筛选，302 与 501 的房态行不展示。"""
    text = "明天401还有房吗，302早餐几点，501呢"
    plan = plan_for(text, ("stay_query", text, dict(ARGS)))
    decision = await _follow_up(text, plan, fallback=fallback, tool="search_availability",
                                rows=_rows("search_availability", (302, 401, 501)))
    reply = decision.reply_text
    assert "401测试房" in reply and "302测试房" not in reply and "501测试房" not in reply
    assert "9:00" in reply and "7:30" in reply


# ── 1.70.4 实施复审 R174-1 至 R174-4 回归 ─────────────────────────────────────

PARK_302 = _snippet(8312, "停车怎么收费？", "302停车每天20元。", category="停车",
                    scope="property", property_id=302)
PARK_401 = _snippet(8412, "停车怎么收费？", "401停车免费。", category="停车",
                    scope="property", property_id=401)


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["missing_middle", "missing_middle_swapped", "newline"])
async def test_unplanned_middle_clause_blocks_inheritance(layout):
    """规划漏掉中间的停车问题时，「302呢」不能跨过它借更早的早餐话题（R174-1）。换行同理。
    邻接在调用前的共享布局里冻结，正常与回退出口读同一份；纯静态计划没有工具后回退出口。"""
    text = ("401早餐几点\n停车怎么收费，302呢" if layout == "newline"
            else "401早餐几点，停车怎么收费，302呢")
    items = [("static_fact", "401早餐几点", {"target_room": "401"}),
             ("static_fact", "302呢", {"target_room": "302"})]
    if layout == "missing_middle_swapped":
        items.reverse()
    if layout == "newline":
        items = [items[0], ("static_fact", "停车怎么收费", {})]
    decision = await _follow_up(text, plan_for(text, *items),
                                entries=[*FU_ENTRIES, PARK_302])
    assert "9:00" not in decision.reply_text
    if layout != "newline":
        assert ("302呢", "scope_unbound") in [(g.text, g.reason) for g in decision.gap_units]


@pytest.mark.asyncio
@pytest.mark.parametrize("swap", [False, True])
async def test_fully_planned_middle_clause_is_the_parent(swap):
    """中间的停车问题有计划项时，「302呢」承接停车而不是早餐（Spec §3.1）。"""
    text = "401早餐几点，停车怎么收费，302呢"
    items = [("static_fact", "401早餐几点", {"target_room": "401"}),
             ("static_fact", "停车怎么收费，302呢", {})]
    plan = plan_for(text, *(reversed(items) if swap else items))
    decision = await _follow_up(text, plan, entries=[*FU_ENTRIES, PARK_302])
    assert "302停车每天20元" in decision.reply_text and "9:00" not in decision.reply_text


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("child", ["static_fact", "stay_query", "catalog_query"])
@pytest.mark.parametrize("returned,covered", [((401, 302, 501), True), ((401,), False)])
async def test_tool_parent_makes_follow_up_a_tool_target_under_any_child_label(
    child, returned, covered, fallback
):
    """「明天401还有房吗，302呢」拆成两项、子项标成哪种类型都是 B：查到 302 就展示，
    没查到说明未查到且不可转交；不再因子项是 static_fact 落成 D（R174-2a）。"""
    text = "明天401还有房吗，302呢"
    plan = plan_for(text, ("stay_query", "明天401还有房吗", {"target_room": "401", **ARGS}),
                    (child, "302呢", {"target_room": "302", **ARGS}))
    referral = [{"unit_id": "2:10-14", "canonical_question": "302房态", "category": "房态"}]
    decision = await _follow_up(text, plan, fallback=fallback, tool="search_availability",
                                rows=_rows("search_availability", returned),
                                decision=_decision(gap_referrals=referral))
    reply = decision.reply_text
    assert "401测试房" in reply and "501测试房" not in reply
    assert ("302测试房" in reply) is covered
    assert [g.reason for g in decision.gap_units] == ([] if covered else ["tool_uncovered"])
    assert not decision.referral_candidates and "9:00" not in reply


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("split", [False, True])
async def test_static_follow_up_is_not_taken_by_a_later_tool_question(split, fallback):
    """「401早餐几点，302呢，明天501还有房吗」：302 承接早餐，不被并入后面的查房（R174-2b）。"""
    text = "401早餐几点，302呢，明天501还有房吗"
    items = ([("static_fact", "401早餐几点，302呢", {}),
              ("stay_query", "明天501还有房吗", {"target_room": "501", **ARGS})] if split
             else [("stay_query", text, {"target_room": "401", **ARGS})])
    decision = await _follow_up(text, plan_for(text, *items), fallback=fallback,
                                tool="search_availability",
                                rows=_rows("search_availability", (401, 302, 501)))
    reply = decision.reply_text
    assert "8:00" in reply and "9:00" in reply and "501测试房" in reply
    assert "302测试房" not in reply and "401测试房" not in reply and not decision.gap_units


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_real_room_enumeration_before_tool_question_is_kept(fallback):
    """对照：真正的枚举「明天302，401还有房吗」不是省略句式，仍并入后面的查房（R172）。"""
    text = "明天302，401还有房吗"
    decision = await _follow_up(text, plan_for(text, ("stay_query", text, dict(ARGS))),
                                fallback=fallback, tool="search_availability",
                                rows=_rows("search_availability", (401,)))
    assert "401测试房" in decision.reply_text
    assert [g.reason for g in decision.gap_units] == ["tool_uncovered"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_own_room_static_clauses_do_not_join_tool_rows(fallback):
    """「明天501还有房吗，401早餐几点，302早餐几点」：房态只展示 501（R174-3）。"""
    text = "明天501还有房吗，401早餐几点，302早餐几点"
    decision = await _follow_up(text, plan_for(text, ("stay_query", text, {
        "target_room": "501", **ARGS})), fallback=fallback, tool="search_availability",
        rows=_rows("search_availability", (401, 302, 501)))
    reply = decision.reply_text
    assert "501测试房" in reply and "401测试房" not in reply and "302测试房" not in reply
    assert "8:00" in reply and "9:00" in reply and not decision.gap_units


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_real_tool_need_with_empty_result_keeps_static_answers(fallback):
    """有真实查房需求但工具返回空：照常说明没查到，静态追问照常作答，不展示别的房间。"""
    text = "明天401还有房吗，302早餐几点，501呢"
    decision = await _follow_up(text, plan_for(text, ("stay_query", text, dict(ARGS))),
                                fallback=fallback, tool="search_availability", rows=[])
    reply = decision.reply_text
    assert "9:00" in reply and "7:30" in reply and "测试房" not in reply
    assert all(g.reason != "knowledge_missing" for g in decision.gap_units)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_catalog_parent_follow_up_is_checked_per_room(fallback):
    """目录父句「有什么房型，302呢」：302 是目录目标，不走静态取证、不问入住日期、不转交。

    目录结果按 302 筛选为空时回「未返回可用资料」，这是 1.70.3 的既有行为，本版不改。
    """
    text = "有什么房型，302呢"
    rows = [{"id": 401, "title": "401房间"}]  # 目录行格式
    decision = await _follow_up(text, plan_for(text, ("catalog_query", text, {})),
                                fallback=fallback, tool="list_properties", rows=rows)
    assert "9:00" not in decision.reply_text and DATE_ASK not in decision.reply_text
    assert all(g.reason != "knowledge_missing" for g in decision.gap_units)
    assert not decision.referral_candidates


@pytest.mark.asyncio
async def test_parent_conflict_does_not_block_follow_up():
    """父句 401 的两条早餐资料互相矛盾时回未确认；302 仍按自己的资料作答（§2.5）。"""
    conflict = _snippet(8403, "早餐几点开始？", "401早餐8:30开始。", category="早餐",
                        scope="property", property_id=401)
    decision = await _follow_up(FU_TEXT, FU_PLANS["P4"](FU_TEXT),
                                entries=[*FU_ENTRIES, conflict])
    assert "9:00" in decision.reply_text
    assert "8:00" not in decision.reply_text and "8:30" not in decision.reply_text
    assert [(g.reason, g.property_id) for g in decision.gap_units] == [("conflict", 401)]


def _record(entry_id, property_id, answer, *, trigger_any=None, trigger_exclude=None):
    """真实知识服务读取的仓储记录：302 的停车资料，可带附加条件词与排除词。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=entry_id, category="停车", scope="property", property_id=property_id,
        valid_from=None, valid_until=None, question_zh="停车怎么收费？", answer_zh=answer,
        question_en="parking", answer_en="parking", keywords=["停车"],
        trigger_any=trigger_any, trigger_exclude=trigger_exclude,
    )


class _ConditionalRepository:
    """真实 `KnowledgeService` 用的内存仓储：302 白天、夜间两条停车资料和一条排除电动车的资料。"""

    def __init__(self, records):
        self.records = records

    async def list_active(self):
        return list(self.records)

    async def list_room_profiles(self):
        return [(pid, str(pid), f"{pid}测试房") for pid in (302, 401, 501)]


@pytest.mark.asyncio
@pytest.mark.parametrize("text,night", [
    ("晚上401停车怎么收费，302呢", True),
    ("晚上几点关门，401停车怎么收费，302呢", False),
])
async def test_follow_up_trigger_comes_from_its_parent_only(text, night):
    """附加条件词只取父问题原文（§2.3）：父句写了「晚上」才用夜间资料，别的问题里的「晚上」
    不能借给追问。真实检索服务，只替换仓储。

    「401晚上」会被房号识别当成「住几晚」的写法而不算房号（既有限制），所以用「晚上401」。
    """
    from homestay_bot.services.knowledge_service import KnowledgeService

    records = [
        _record(2, 302, "302晚上停车免费。", trigger_any=["晚上"]),
        _record(3, 401, "401停车每小时5元。"),
    ]
    first = text.rsplit("，", 1)[0]
    items = ([("static_fact", first, {}), ("static_fact", "302呢", {})] if night else
             [("static_fact", "晚上几点关门", {}), ("static_fact", "401停车怎么收费，302呢", {})])
    decision = await _follow_up(text, plan_for(text, *items),
                                knowledge=KnowledgeService(_ConditionalRepository(records)))
    assert ("302晚上停车免费" in decision.reply_text) is night


@pytest.mark.asyncio
async def test_follow_up_exclusion_uses_whole_turn_text():
    """排除词按本轮全文判断：全文提到电动车时，排除电动车的 302 资料不能给追问作答。"""
    from homestay_bot.services.knowledge_service import KnowledgeService

    records = [
        _record(1, 302, "302停车每天20元。", trigger_exclude=["电动车"]),
        _record(3, 401, "401停车每小时5元。"),
    ]
    text = "我开电动车，401停车怎么收费，302呢"
    plan = plan_for(text, ("static_fact", "我开电动车，401停车怎么收费，302呢", {}))
    decision = await _follow_up(text, plan,
                                knowledge=KnowledgeService(_ConditionalRepository(records)))
    assert "302停车每天20元" not in decision.reply_text


@pytest.mark.asyncio
async def test_budget_skipped_unit_is_budget_not_missing_and_not_referable():
    """真实检索：合并信封放不下 501 的资料时记 budget，模型选中编号也不能转交（R174-4）。"""
    from homestay_bot.integrations.deepseek_client import GapReferral
    from homestay_bot.services.knowledge_service import KnowledgeService
    from homestay_bot.tools.reply_regression import _Entry, _MemoryKnowledge

    entries = [_Entry(9000, "早餐", "早餐几点开始？", "早餐说明" + "甲" * 11650,
                      "Breakfast", "Details", scope="global")]
    entries += [_Entry(pid, "早餐", "早餐几点开始？", f"{pid}早餐9:00开始。" + "乙" * 2700,
                       "Breakfast", "Details", scope="property", property_id=pid)
                for pid in (401, 302, 501)]
    knowledge = KnowledgeService(_MemoryKnowledge(
        entries, [{"id": pid, "room_number": str(pid), "title": ""} for pid in (401, 302, 501)]
    ))
    text = "401早餐几点，302呢，501呢"
    assistant, _ = _assistant([_decision()], knowledge)
    decision = await _respond(assistant, text,
                              plan_for(text, ("static_fact", text, {"target_room": "401"})))
    [gap] = [g for g in decision.gap_units if g.property_id == 501]
    assert gap.reason == "budget"
    selected = decision.model_copy(update={"gap_referrals": [GapReferral(
        unit_id=gap.unit_id, canonical_question="501早餐几点", category="早餐")]})
    assert assistant._referral_candidates(selected) == []


@pytest.mark.asyncio
async def test_single_retrieval_budget_skip_is_budget():
    """单次检索预算放不下（单条超过 3000 字）同样记 budget；真实空库对照记 knowledge_missing。"""
    from homestay_bot.services.knowledge_service import KnowledgeService
    from homestay_bot.tools.reply_regression import _Entry, _MemoryKnowledge

    profiles = [{"id": pid, "room_number": str(pid), "title": ""} for pid in (401, 302)]
    base = [_Entry(401, "早餐", "早餐几点开始？", "401早餐8:00开始。", "Breakfast", "Details",
                   scope="property", property_id=401)]
    huge = _Entry(302, "早餐", "早餐几点开始？", "302早餐9:00开始。" + "乙" * 3100,
                  "Breakfast", "Details", scope="property", property_id=302)
    reasons = []
    for entries in ([*base, huge], base):
        assistant, _ = _assistant([_decision()],
                                  KnowledgeService(_MemoryKnowledge(entries, profiles)))
        decision = await _respond(assistant, FU_TEXT, FU_PLANS["P4"](FU_TEXT))
        reasons.append([g.reason for g in decision.gap_units if g.property_id == 302])
    assert reasons == [["budget"], ["knowledge_missing"]]


EXTRA_BED = [
    _snippet(8421, "可以加床吗？", "401可以加床，每晚100元。", category="加床",
             scope="property", property_id=401),
    _snippet(8321, "可以加床吗？", "302不能加床。", category="加床",
             scope="property", property_id=302),
    _snippet(8422, "能延迟退房吗？", "401可以延迟退房，需提前确认。", category="退房",
             scope="property", property_id=401),
    _snippet(8322, "能延迟退房吗？", "302可以延迟退房，需提前确认。", category="退房",
             scope="property", property_id=302),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("parent,answer", [
    ("401可以加床吗", "302不能加床"), ("401能延迟退房吗", "302可以延迟退房"),
])
async def test_policy_inquiry_parent_is_inherited(parent, answer):
    """政策咨询「401可以加床吗，302呢」：词面像服务请求也照常承接，302 按自己资料作答（F174-1）。"""
    text = f"{parent}，302呢"
    decision = await _follow_up(text, plan_for(text, ("static_fact", text, {})),
                                entries=EXTRA_BED)
    assert answer in decision.reply_text and not decision.gap_units


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["401可以加床吗，302呢", "麻烦送两瓶水，302呢"])
async def test_action_like_parent_follow_up_missing_is_not_referred(text):
    """父句词面像动作（政策咨询或被误标成静态的请求）、302 真缺资料：只回未确认，模型选中
    编号也不转交，避免误标的动作变成资料核实卡片（F174-1 的保守范围）。"""
    fragment = "302呢"
    unit = f"1:{text.index(fragment)}-{text.index(fragment) + len(fragment)}"
    referral = [{"unit_id": unit, "canonical_question": "302加床", "category": "加床"}]
    decision = await _follow_up(text, plan_for(text, ("static_fact", text, {})),
                                entries=EXTRA_BED[:1],
                                decision=_decision(gap_referrals=referral))
    assert [g.reason for g in decision.gap_units if g.unit_id == unit] == ["scope_unbound"]
    assert unit not in [c.unit.unit_id for c in decision.referral_candidates]


@pytest.mark.asyncio
async def test_planned_service_request_parent_still_blocks():
    """对照：规划把「麻烦送两瓶水」正确标为服务事项时，它是非事实项，「302呢」不承接（D）。"""
    text = "麻烦送两瓶水，302呢"
    plan = plan_for(text, ("service_request", "麻烦送两瓶水"), ("static_fact", "302呢"))
    decision = await _follow_up(text, plan, entries=EXTRA_BED)
    assert ("302呢", "scope_unbound") in [(g.text, g.reason) for g in decision.gap_units]


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_each_tool_result_is_filtered_by_its_own_fact_targets(fallback):
    """401 只问房态、302 只问价：房态只列 401、参考价只列 302，不交叉展示（F174-2）。"""
    text = "明天401有房吗，明天302多少钱，501早餐几点，401呢"
    plan = plan_for(text, ("stay_query", "明天401有房吗", {"target_room": "401", **ARGS}),
                    ("stay_query", "明天302多少钱", {"target_room": "302", **ARGS}),
                    ("static_fact", "501早餐几点，401呢", {}))
    rows = {"search_availability": _rows("search_availability", (401, 302, 501)),
            "search_reference_price": _rows("search_reference_price", (401, 302, 501))}
    responses = [_tool_message("search_availability", ARGS),
                 _tool_message("search_reference_price", ARGS),
                 "invalid" if fallback else _decision()]
    assistant, _ = _assistant(responses, _Knowledge(list(FU_ENTRIES), FU_ROOMS), _Tools(rows))
    decision = await _respond(assistant, text, plan)
    reply = decision.reply_text
    assert "7:30" in reply and "8:00" in reply
    assert "可订：401测试房" in reply and "302测试房：" in reply
    assert "可订：401测试房、302测试房" not in reply and "401测试房：10月8日" not in reply
    assert "501测试房" not in reply and not decision.gap_units


@pytest.mark.asyncio
async def test_parent_period_veto_does_not_block_follow_up():
    """父句 401 被特殊时期资料否决（period_veto）时回未确认；302 仍按自己的资料作答（§2.5）。"""
    veto = _snippet(8404, "二楼早餐厅几点开放？", "401二楼早餐厅国庆期间10:00开放。",
                    category="早餐", scope="property", property_id=401,
                    valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 7))
    decision = await _follow_up(FU_TEXT, FU_PLANS["P4"](FU_TEXT), entries=[*FU_ENTRIES, veto])
    assert "9:00" in decision.reply_text and "8:00" not in decision.reply_text
    assert [(g.reason, g.property_id) for g in decision.gap_units] == [("period_veto", 401)]
