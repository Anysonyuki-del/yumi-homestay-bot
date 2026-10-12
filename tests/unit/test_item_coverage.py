"""小句覆盖判定（修复 Spec §2.1(2)）：所需事实、锚点与剩余、日期角色、按证据判定覆盖。"""

from datetime import UTC, date, datetime

import pytest

from homestay_bot.services.answer_policy import asks_room_price, asks_stay_availability
from homestay_bot.services.item_coverage import (
    AVAILABILITY_ANCHORS,
    CATALOG_ANCHORS,
    PRICE_ANCHORS,
    ClauseTarget,
    asks_property_catalog,
    clause_dates,
    clause_needs,
    clause_rooms,
    clause_target,
    evidence_covers,
    intersecting_clauses,
    missing_nights,
    split_clauses,
    static_clause_dates,
)
from homestay_bot.services.reply_plan import ReplyEvidence


@pytest.mark.parametrize("text,rooms", [
    ("2026-10-10 401房间", ("401",)), ("10月10日到12日 room 401", ("401",)),
    ("2026-10-10到2026-10-12的1401房", ("1401",)),
    ("插座220V和1500W，房费500元", ()),
])
def test_date_spans_are_not_room_numbers(text, rooms):
    """年份与日期不是房号，真实三四位房号以及单位排除口径保持不变。"""
    assert clause_rooms(text) == rooms


@pytest.mark.parametrize("text,expected", [
    ("早餐几点", (date(2026, 10, 10), date(2026, 10, 11))),
    ("10月11日早餐几点", (date(2026, 10, 11), date(2026, 10, 11))),
    ("10月10日入住10月12日退房的早餐", (date(2026, 10, 10), date(2026, 10, 11))),
    ("10月12日早餐几点", None), ("10月12日退房的早餐", None),
    ("10月10日到11日早餐几点", None), ("10月11日到10日早餐几点", None),
    ("10月10日和明天早餐几点", None),
])
def test_static_date_binding_respects_day_and_endpoint_role(text, expected):
    """已有区间内单日只核当天，另一段住宿或含糊日期不借用候选。"""
    assert static_clause_dates(text, (date(2026, 10, 10), date(2026, 10, 11)), 2026) == expected


@pytest.mark.parametrize("text,expected", [
    ("今天早餐几点", date(2026, 12, 31)),
    ("明天早餐几点", date(2027, 1, 1)),
    ("Tomorrow breakfast", date(2027, 1, 1)),
    ("后天早餐几点", date(2027, 1, 2)),
    ("明天明日早餐几点", date(2027, 1, 1)),
    ("今天和明天早餐几点", None), ("今明两天早餐几点", None),
    ("明后天早餐几点", None), ("大后天早餐几点", None),
    ("第二天早餐几点", None), ("第2天早餐几点", None), ("第三十天早餐几点", None),
    ("次日早餐几点", None), ("翌日早餐几点", None), ("周末早餐几点", None),
    ("the next day breakfast", None), ("second day breakfast", None),
    ("day 2 breakfast", None), ("2nd day breakfast", None),
    ("12月31日和明天早餐几点", None),
])
def test_relative_static_day_uses_today_or_stays_unbound(text, expected):
    """日历单日用实际今日跨年计算，范围或无锚点序数日不能偷用检索起点。"""
    bound = static_clause_dates(
        text, (date(2026, 12, 31), date(2027, 1, 2)), 2026, today=date(2026, 12, 31),
    )
    assert bound == ((expected, expected) if expected else None)


def test_calendar_day_must_be_in_existing_scope_and_have_a_clock():
    """今日缺失或明天落在范围外时，不借父日期也不额外扩大检索。"""
    dates = (date(2026, 10, 10), date(2026, 10, 11))
    assert static_clause_dates("明天早餐", dates, 2026) is None
    assert static_clause_dates("明天早餐", dates, 2026, today=date(2026, 10, 7)) is None


@pytest.mark.parametrize("weekday,name", list(enumerate("一二三四五六日")))
def test_this_week_day_has_one_calendar_anchor(weekday, name):
    """本周按周一开始，允许已过去的日期；跨年仍绑定同一自然周，周日和周天等价。"""
    from datetime import timedelta

    start = date(2026, 12, 28)
    expected = start + timedelta(days=weekday)
    for prefix in ["本周", "这周"]:
        assert static_clause_dates(
            f"{prefix}{name}能延迟退房吗", (start, date(2027, 1, 3)), 2026,
            today=date(2026, 12, 31),
        ) == (expected, expected)
    if weekday == 6:
        assert static_clause_dates(
            "这周天能延迟退房吗", (start, date(2027, 1, 3)), 2026,
            today=date(2026, 12, 31),
        ) == (expected, expected)


@pytest.mark.parametrize(
    "clause",
    [
        "10月10日401房间住一晚多少钱",
        "明晚201多少钱",
        "明天还有房吗",
        "这周末有空房吗",
        "10月3日到5日住两晚多少钱",
        "房费怎么算",
        "今晚能住吗",
        "明天401还能订吗",
        "最便宜的房间多少钱",
        "你们有哪些房型",
        "what rooms do you have",
        "Is there a room available tomorrow",
        "10月10日入住10月12日退房有房吗",
    ],
)
def test_pure_tool_questions_leave_no_residue(clause: str) -> None:
    """纯问房价、房态、房型目录的常见说法不多出剩余内容（不多发一句未确认）。"""
    needs = clause_needs(clause)
    assert needs.tool_facts
    assert needs.residue == ""


@pytest.mark.parametrize(
    ("clause", "residue"),
    [
        ("多少钱而且能看江景吗", "看江景"),
        ("有江景的房吗", "江景"),
        ("有朝南的房吗", "朝南"),
        ("介绍能看江景的房", "看江景"),
        ("有哪些房间可以看江景", "看江景"),
    ],
)
def test_wildcard_parts_of_recognizers_are_kept_as_residue(clause: str, residue: str) -> None:
    """识别正则的通配部分没有证据，不能随整段命中删掉（Codex 审查 V5-R1）。"""
    assert clause_needs(clause).residue == residue


def test_coarse_item_keeps_the_static_question_as_residue() -> None:
    """同一小句里问价格、房态之外的早餐，剩余就是早餐问题（V3-R1）。"""
    needs = clause_needs("10月3日401房间住一晚多少钱，有房吗，早餐几点开始")
    assert needs.price and needs.availability
    assert needs.residue == "早餐几点开始"


# 每个锚点放进它真实出现的语境；英文锚点用英文句子，目录锚点按识别正则的结构给句子。
_ANCHOR_CONTEXTS = {
    "available": "Is room 401 available tomorrow",
    "availability": "Any availability tomorrow",
    "vacancy": "Any vacancy tomorrow",
    "vacancies": "Any vacancies tomorrow",
    "空着": "明天401房还空着吗",
    "how much": "How much for room 401 tomorrow",
    "prices": "What are the prices tomorrow",
    "price": "What is the price tomorrow",
    "rates": "What are the rates tomorrow",
    "rate": "What is the rate tomorrow",
    "costs": "What are the costs tomorrow",
    "cost": "What does it cost tomorrow",
    "cheapest": "Which room is cheapest tomorrow",
    "room types": "What room types do you have",
    "room type": "Which room type do you have",
    "introduce": "Please introduce the room names",
    "details": "Show me the room details",
    "names": "Show me the room names",
    "介绍一下": "介绍一下你们的房间",
    "介绍": "介绍你们的房间",
    "详情": "房间详情",
    "名称": "房间名称",
}


@pytest.mark.parametrize(
    ("anchors", "recognizer", "context"),
    [
        (AVAILABILITY_ANCHORS, asks_stay_availability, "明天{}吗"),
        (PRICE_ANCHORS, asks_room_price, "明晚住一晚{}"),
        (CATALOG_ANCHORS, asks_property_catalog, "你们有什么{}"),
    ],
)
def test_each_anchor_triggers_its_recognizer_in_context(anchors, recognizer, context) -> None:
    """锚点清单与识别正则会漂移：每个锚点放进真实语境都要触发对应识别函数。"""
    for anchor in anchors:
        sentence = _ANCHOR_CONTEXTS.get(anchor, context.format(anchor))
        assert recognizer(sentence), anchor


def test_date_roles_are_kept() -> None:
    """入住、退房端点分别识别；倒置区间与两段区间无法绑定（V5-R2）。"""
    pair = clause_dates("10月10日入住10月12日退房有房吗", 2026)
    assert (pair.check_in, pair.check_out) == (date(2026, 10, 10), date(2026, 10, 12))
    short = clause_dates("10月3日到5日住两晚多少钱", 2026)
    assert (short.check_in, short.check_out) == (date(2026, 10, 3), date(2026, 10, 5))
    assert clause_dates("10月12日退房", 2026).check_out == date(2026, 10, 12)
    assert clause_dates("10月12日到10月10日有房吗", 2026).unbindable
    assert clause_dates("10月1日到3日，10月5日到7日", 2026).unbindable
    assert clause_dates("明天有房吗", 2026).relative


def test_clauses_and_item_intersection() -> None:
    """切句保留位置；取与计划项区间相交的小句，空区间不算覆盖。"""
    text = "今天天气怎么样，武汉有哪些经典景点推荐"
    clauses = split_clauses(text)
    assert [clause.text for clause in clauses] == ["今天天气怎么样", "武汉有哪些经典景点推荐"]
    assert [c.text for c in intersecting_clauses(clauses, 2, 7)] == [
        "今天天气怎么样",
        "武汉有哪些经典景点推荐",
    ]
    assert intersecting_clauses(clauses, 0, 0) == []


def _evidence(kind: str, property_id: int, start: date, end: date) -> ReplyEvidence:
    """构造一行工具证据。"""
    return ReplyEvidence(
        source_kind=kind,  # type: ignore[arg-type]
        source_id=f"{kind}:{property_id}",
        property_id=property_id,
        target_date=start,
        target_end_date=end,
        fetched_at=datetime.now(UTC),
    )


def _target(clause: str, **overrides) -> ClauseTarget:
    """按一个目标为 401、10 月 10–11 日的计划项求小句目标。"""
    arguments = {
        "item_rooms": ("401",),
        "item_relative_dates": False,
        "item_date_names": 1,
        "item_check_in": date(2026, 10, 10),
        "item_check_out": date(2026, 10, 11),
        "room_properties": {"401": 1401, "402": 1402},
        "item_property": 1401,
        "year": 2026,
    }
    arguments.update(overrides)
    return clause_target(clause, **arguments)


def test_other_named_room_or_date_is_not_covered_by_the_item_target() -> None:
    """小句点名的另一间房、另一个入住日不能继承计划项目标（Codex 审查 V4-R1）。"""
    rows = [_evidence("availability", 1401, date(2026, 10, 10), date(2026, 10, 11))]
    assert evidence_covers(rows, "availability", _target("10月10日401有房吗"))
    two_rooms = _target("402有房吗", item_rooms=("401", "402"))
    assert not evidence_covers(rows, "availability", two_rooms)
    assert not evidence_covers(rows, "availability", _target("10月11日401有房吗"))
    pair = [_evidence("availability", 1401, date(2026, 10, 10), date(2026, 10, 12))]
    assert evidence_covers(pair, "availability", _target("10月10日入住10月12日退房有房吗"))
    assert not evidence_covers(rows, "availability", _target("10月10日入住10月12日退房有房吗"))


def test_unmapped_room_and_relative_with_several_dates_are_unbound() -> None:
    """映射不到的房号没有证据；多个日期写法里夹着相对日期时无法证明继承。"""
    rows = [_evidence("availability", 1401, date(2026, 10, 10), date(2026, 10, 11))]
    assert not evidence_covers(rows, "availability", _target("909有房吗"))
    target = _target("明天有房吗", item_date_names=2, item_relative_dates=True)
    assert target.unbound


def test_missing_nights_only_counts_the_asked_range() -> None:
    """逐夜核对所问区间；区间外夜价不能补齐（Codex 审查 V6-R3）。"""
    nights = [
        {"date": "2026-10-10", "price": 300},
        {"date": "2026-10-13", "price": 280},
    ]
    assert missing_nights(nights, date(2026, 10, 10), date(2026, 10, 12)) == [date(2026, 10, 11)]
    assert missing_nights(nights[:1], date(2026, 10, 10), date(2026, 10, 11)) == []


@pytest.mark.parametrize(
    ("text", "rooms"),
    [
        ("插座是220V的吗", ()),
        ("能用1500W的吹风机吗", ()),
        ("房间1000米内有地铁吗", ()),
        ("2026年10月有房吗", ()),
        ("Is room 401 available tomorrow", ("401",)),
        ("201号房几点退房", ("201",)),
        ("明晚402多少钱", ("402",)),
    ],
)
def test_room_numbers_exclude_units(text: str, rooms: tuple[str, ...]) -> None:
    """紧跟字母或度量单位的数字不是房号（1.70.0 候选门禁 K-用电-D「220V」）；真房号照常识别。"""
    from homestay_bot.services.item_coverage import clause_rooms

    assert clause_rooms(text) == rooms
