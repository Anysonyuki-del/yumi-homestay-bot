"""计划项的小句覆盖判定：工具与澄清分项只覆盖它实际回答的那一类事实（修复 Spec §2.1(2)）。

一个粗粒度计划项可能在同一句里同时问房价、房态、房型目录和本店静态事实。以前只要有一个
日期相符的工具分项就当整项答完，早餐、江景这类子问随之丢失（Codex 审查 V3-R1、V3-R2）。
本模块把计划项按原文区间切成小句，每个小句按现有正向识别得出它要的事实（可以同时要多类），
再按实际证据行的房源与日期判断是否覆盖；正向识别的整段命中不当作证据，只删除被证据覆盖的
固定操作锚点，剩下的内容按本店静态问题处理。

纯函数，不调用模型或外部服务；适配器在调用前算出小句与剩余，调用后按工具证据判定覆盖。
不新增规划器或通用语法解析器：静态日历相对日期按实际今日绑定，未解析的日期不得继承项范围。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from homestay_bot.services.answer_policy import asks_room_price, asks_stay_availability
from homestay_bot.services.knowledge_service import normalize_text
from homestay_bot.services.reply_plan import ReplyEvidence

# 回复链路切分子问题的统一正则：联网分组、证据计划与小句覆盖都用这一个，保证同一次切句。
CLAUSE_SPLIT = re.compile(r"[，,。；;！？!?\n]|另外|以及|同时")

# 住宿日期写法：工具开放、独立房态判定与小句目标共用这一个定义。
EXPLICIT_STAY_DATE_PATTERN = re.compile(
    r"今天|今晚|今日|明天|明晚|明日|后天|"
    r"本周[一二三四五六日天]|这周[一二三四五六日天]|周末|"
    r"\d{1,2}月\d{1,2}[日号]|\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2}|"
    r"\b(?:today|tonight|tomorrow)\b",
    re.IGNORECASE,
)

# 问本店有哪些、哪几种房或介绍房间：必须先读取百居易房源名称。按意图归类，而不是只认
# 「介绍」字眼：「你们有哪些房型」曾因不含「介绍/详情/名称」拿不到工具，只能回尚未确认。
# 房态（还有房吗）和房内设施（房间有空调吗）不属于房型列表。
_PROPERTY_CATALOG_PATTERN = re.compile(
    r"房型|户型|房源|房间类型|"
    r"(?:哪些|哪几种|哪种|什么|几种|几类|多少种)(?:样的)?房|"
    r"房间?(?:都有|有)(?:哪些|哪几种|几种|什么类型)|"
    r"介绍.*房|房间.*(?:介绍|详情|名称)|"
    r"room.*(?:intro|detail|name)|room types?|what rooms|which rooms",
    re.IGNORECASE,
)

# 各识别函数的固定操作锚点：只有该类事实确实有对应证据时才删除（修复 Spec §2.1(2)「未知
# 剩余」）。识别正则里的通配部分（「有江景的房」里的「江景」、「介绍能看江景的房」里的
# 「能看江景」）从来没有获得证据，不能随整段命中一起删掉。
# ponytail: 锚点清单是识别器的固定词，与正则分开维护会漂移，测试逐个断言每个锚点在语境中
# 能触发对应识别函数。天花板：不在清单里的操作说法会多一句未确认（安全方向）；出现时只补
# 操作锚点或形式词，不补领域词。
AVAILABILITY_ANCHORS = (
    "还有空房", "还有房", "有空房", "空房", "余房", "剩房", "有房", "满房", "订满", "几间房",
    "房态", "还能订", "能订", "可订", "可用房", "还能住", "能住", "可以住", "住得下", "空着",
    "available", "availability", "vacancy", "vacancies",
)
PRICE_ANCHORS = (
    "多少钱", "几多钱", "房价", "房费", "参考价", "住宿价格", "民宿价格",
    "房型价格", "最低价", "最便宜", "价格", "价钱",
    "how much", "prices", "price", "rates", "rate", "costs", "cost", "cheapest",
)
CATALOG_ANCHORS = (
    "房间类型", "房型", "户型", "房源", "介绍一下", "介绍", "详情", "名称",
    "room types", "room type", "introduce", "details", "names",
)
# 指房间本身的名词与小句目标一同视为「问的是哪间房」，不属于剩余内容。
_ROOM_NOUNS = ("号房间", "号房", "房间", "客房", "房", "rooms", "room")
# 纯形式词：代词、疑问、语气与连接词，不收领域词、否定、数值或限定（与 D-G9 共用）。
_FORM_WORDS_ZH = (
    "你们", "您们", "你家", "贵店", "民宿", "店里", "这边", "这里", "那边", "我们", "咱们",
    "请问", "麻烦", "一下", "怎么算", "哪几种", "哪种", "哪些", "什么", "几种", "几类",
    "多少种", "样的",
    "而且", "并且", "还有", "然后", "那么", "的话", "可以", "能", "想", "要", "问", "请",
    "我", "这", "那", "都", "有", "还", "的", "吗", "呢", "吧", "啊", "呀", "嘛", "哦", "了",
    "住", "一共", "总共",
)
_FORM_WORDS_EN = re.compile(
    r"\b(?:what|which|do|does|did|you|your|have|has|any|are|is|there|a|an|the|please|show|"
    r"me|list|tell|about|for|i|we|our|can|could|would|like|to|it|of|in|on|how|many|"
    r"and|also|still|some)\b",
    re.IGNORECASE,
)
_NIGHTS_PATTERN = re.compile(
    r"住?[一两二三四五六七八九十\d]+(?:个)?(?:晚上|晚|夜|天)|\b\d+\s+nights?\b",
    re.IGNORECASE,
)
# 三到四位数字房号。紧跟字母（220V、1500W）或金额、日期、数量、度量单位时不是房号，口径与
# `_unmatched_room_header` 一致并补度量单位（1.70.0 候选门禁 K-用电-D：「插座是220V的吗」
# 被当成问 220 房间，映射不到而整项回未确认）。字母前不允许空白：「room 401 available」
# 里的 401 仍是房号。ponytail: 只排除常见单位，新的单位写法出现时补进来并补正反样本。
_ROOM_NUMBER = re.compile(
    r"(?<![\dA-Za-z])\d{3,4}"
    r"(?!\d|[A-Za-z]|\s*(?:元|块|年|月|日|晚|人|点|分|%|米|度|伏|瓦|毫|千|万|平|岁|斤|小时|秒))"
)
_ABSOLUTE_DATE = re.compile(
    r"(?:(?P<year>\d{4})-(?P<ym>\d{2})-(?P<yd>\d{2}))"
    r"|(?:(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号])"
    r"|(?:(?P<sm>\d{1,2})/(?P<sd>\d{1,2}))"
)
# 区间尾部可以简写为「5日」「5号」，按区间开头的月份补全。
_RANGE_TAIL = re.compile(r"\s*(?:到|至|~|～|-|—)\s*(?P<day>\d{1,2})[日号]")
_RANGE_JOIN = re.compile(r"^\s*(?:到|至|~|～|-|—)\s*$")
_CHECK_OUT_MARK = re.compile(r"^\s*退房|^\s*离店|^\s*check.?out", re.IGNORECASE)
_RELATIVE_DATE = re.compile(
    # 序数日只用于识别未绑定日期，不能把「第二天」当作无日期问题继承整个住宿区间。
    r"今明(?:两|2)?天|明后天|大后天|今天|今晚|今日|明天|明晚|明日|后天|后日|"
    r"第[零〇一二两三四五六七八九十百\d]+[天日]|次日|翌日|"
    r"本周[一二三四五六日天]|这周[一二三四五六日天]|周末|"
    r"(?<![A-Za-z0-9])(?:today|tonight|tomorrow|(?:the\s+)?next\s+day|"
    r"(?:the\s+)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"\d+(?:st|nd|rd|th))\s+day|day\s+\d+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# 「周末」前的限定词：空、这、这个、本表示本周，下、下个表示下周；「上」「下下」等不支持，
# 一并捕获后拒绝，不能只截出后面的「周末」。
_WEEKEND = re.compile(r"(?P<qualifier>下下个?|上上?个?|下个?|这个?|本)?周末")
_WEEKEND_OFFSETS = {"": 0, "这": 0, "这个": 0, "本": 0, "下": 7, "下个": 7}
# 限定词之前若紧挨着没被消费的时间修饰（「下周的」「上一个」「下个月」「第二个」「节假日」），
# 说明客人问的不是本周或下周末；拒绝，不能退化成裸「周末」按本周作答（实施复审 I71-R1）。
_WEEKEND_FOREIGN_PREFIX = re.compile(
    r"(?:[上下前后这那本该]|[一二三四五六七八九十两几\d]|个|周|星期|礼拜|月|年|号|日|天|底|初|旬|节|假期?)"
    r"的?\s*$"
)
# 日历相对词和住宿序数日的锚点不同；这里只解析不依赖入住上下文的确定单日。
_CALENDAR_DAY_OFFSETS = {
    "今天": 0, "今晚": 0, "今日": 0, "today": 0, "tonight": 0,
    "明天": 1, "明晚": 1, "明日": 1, "tomorrow": 1,
    "后天": 2, "后日": 2,
}
_NOISE = re.compile(r"[\W_]+")


def asks_property_catalog(text: str) -> bool:
    """是否在问本店有哪些、哪几种房或介绍房间（房源目录只回答这一类事实）。"""
    return _PROPERTY_CATALOG_PATTERN.search(text) is not None


@dataclass(frozen=True)
class Clause:
    """原文里的一个小句及其位置；切句只做一次，前后阶段用同一组区间与编号。"""

    text: str
    start: int
    end: int


def split_clauses(text: str) -> list[Clause]:
    """按回复链路统一的分隔符切出非空小句，保留各小句在原文中的位置。"""
    clauses: list[Clause] = []
    position = 0
    for match in [*CLAUSE_SPLIT.finditer(text), None]:
        stop = match.start() if match is not None else len(text)
        piece = text[position:stop]
        stripped = piece.strip()
        if stripped:
            offset = position + piece.index(stripped)
            clauses.append(Clause(stripped, offset, offset + len(stripped)))
        if match is not None:
            position = match.end()
    return clauses


def intersecting_clauses(clauses: Sequence[Clause], start: int, length: int) -> list[Clause]:
    """与计划项原文区间相交的全部小句（不只数完全落在区间内的小句）；区间为空时返回空。"""
    if length <= 0:
        return []
    end = start + length
    return [clause for clause in clauses if clause.start < end and clause.end > start]


@dataclass(frozen=True)
class StayDates:
    """小句点名的绝对日期与角色；`unbindable` 表示写法无法可靠绑定到一段住宿。"""

    check_in: date | None = None
    check_out: date | None = None
    relative: bool = False
    unbindable: bool = False

    @property
    def named(self) -> bool:
        """小句是否点名了任何日期（绝对或相对）。"""
        return self.check_in is not None or self.check_out is not None or self.relative


def _absolute_date(match: re.Match[str], year: int) -> date | None:
    """把一个绝对日期写法换成日期；不是合法日期时返回空。"""
    try:
        if match.group("year"):
            return date(int(match.group("year")), int(match.group("ym")), int(match.group("yd")))
        if match.group("month"):
            return date(year, int(match.group("month")), int(match.group("day")))
        return date(year, int(match.group("sm")), int(match.group("sd")))
    except ValueError:
        return None


def clause_dates(text: str, year: int) -> StayDates:
    """识别小句里的住宿日期及端点角色（修复 Spec §2.1(2)「小句目标」，V5-R2）。

    - 「A入住…B退房」「A到/至/-B」是一对区间，B 可简写为「5日」并按 A 的月份补全；
    - 单个日期按入住日；后面紧跟「退房」时按退房日；
    - 区间 B 早于或等于 A、出现两段不同区间、日期不合法时视为无法绑定；
    - 年份取调用方给出的年份（计划项已核验日期的年份）；不建通用日期解析器。
    """
    matches = list(_ABSOLUTE_DATE.finditer(text))
    relative = _RELATIVE_DATE.search(text) is not None
    if not matches:
        return StayDates(relative=relative)
    dates = [_absolute_date(match, year) for match in matches]
    if any(value is None for value in dates):
        return StayDates(relative=relative, unbindable=True)
    first = matches[0]
    first_date = dates[0]
    assert first_date is not None
    if len(matches) == 1:
        tail = _RANGE_TAIL.match(text, first.end())
        if tail is not None:
            try:
                second = first_date.replace(day=int(tail.group("day")))
            except ValueError:
                return StayDates(relative=relative, unbindable=True)
            if second <= first_date:
                return StayDates(relative=relative, unbindable=True)
            return StayDates(first_date, second, relative)
        if _CHECK_OUT_MARK.search(text[first.end():first.end() + 4]):
            return StayDates(check_out=first_date, relative=relative)
        return StayDates(check_in=first_date, relative=relative)
    if len(matches) == 2:
        end_date = dates[1]
        assert end_date is not None
        between = text[first.end():matches[1].start()]
        if _RANGE_JOIN.match(between) or "入住" in between or "check" in between.lower():
            if end_date <= first_date:
                return StayDates(relative=relative, unbindable=True)
            return StayDates(first_date, end_date, relative)
    return StayDates(relative=relative, unbindable=True)


def clause_rooms(text: str) -> tuple[str, ...]:
    """排除日期区间后识别真实房号，避免 ISO 年份参与范围核对。"""
    text = _RANGE_TAIL.sub(" ", _ABSOLUTE_DATE.sub(" ", text))
    return tuple(dict.fromkeys(_ROOM_NUMBER.findall(text)))


def static_clause_dates(
    text: str, dates: tuple[date | None, date | None], year: int,
    *, today: date | None = None,
) -> tuple[date | None, date | None] | None:
    """静态小句只核所问单日；日历词用实际今日，无法确定的相对日期不借父区间。"""
    named = clause_dates(text, year)
    if named.unbindable or (named.relative and (named.check_in or named.check_out)):
        return None
    start, end = dates
    if named.check_in and named.check_out:
        return dates if (named.check_in, named.check_out - timedelta(days=1)) == dates else None
    point = named.check_in or named.check_out
    if named.relative:
        point = relative_calendar_day(text, today) if today is not None else None
        if point is None:
            # 周末是唯一支持的日历范围（周末 Spec §2.2）：必须完整落在项目标内才绑定，
            # 只相交一天不算，避免拿住宿区间里的周六资料回答整个周末。
            weekend = relative_calendar_weekend(text, today) if today is not None else None
            if weekend and start and start <= weekend[0] and weekend[1] <= (end or start):
                return weekend
            # shortcut: 住宿序数日等范围未解析；有可靠锚点的新契约再扩展，不能猜入住日。
            return None
    if point is not None:
        return (point, point) if start and start <= point <= (end or start) else None
    return dates


def relative_calendar_day(text: str, today: date) -> date | None:
    """只解析与实际今日有关的一个自然日；本周从周一开始，序数、范围或不同单日拒绝。"""
    points: set[date] = set()
    for match in _RELATIVE_DATE.finditer(text):
        token = match[0].lower()
        offset = _CALENDAR_DAY_OFFSETS.get(token)
        if offset is None:
            weekday = re.fullmatch(r"(?:本周|这周)([一二三四五六日天])", token)
            if weekday is None:
                return None
            offset = "一二三四五六日".index(weekday[1].replace("天", "日")) - today.weekday()
        points.add(today + timedelta(days=offset))
    return next(iter(points)) if len(points) == 1 else None


def relative_calendar_weekend(text: str, today: date) -> tuple[date, date] | None:
    """解析中文日历周末，返回含首尾的自然日范围（周末 Spec §2.1）。

    - 本周从周一起算：「周末／这周末／这个周末／本周末」取本周六至周日；今天是周日时只取今天
      （D-W2：已过去的周六不再核）；
    - 「下周末／下个周末」始终是下周六至周日，不在裁剪后的本周末上再加 7 天；
    - 同一句出现绝对日期、其他相对日期或序数日、本周与下周两种周末，「上周末」「下下周末」
      这类未支持的限定，或「下周的周末」「上一个周末」这类限定词前另有时间修饰时拒绝，
      不截出其中的「周末」字样当作本周末。
    """
    if _ABSOLUTE_DATE.search(text):
        return None
    if any(match[0] != "周末" for match in _RELATIVE_DATE.finditer(text)):
        return None
    offsets: set[int] = set()
    for match in _WEEKEND.finditer(text):
        qualifier = match["qualifier"] or ""
        if qualifier not in _WEEKEND_OFFSETS or _WEEKEND_FOREIGN_PREFIX.search(
            text, 0, match.start()
        ):
            return None
        offsets.add(_WEEKEND_OFFSETS[qualifier])
    if len(offsets) != 1:
        return None
    saturday = today + timedelta(days=5 - today.weekday())
    if offsets == {7}:
        return saturday + timedelta(days=7), saturday + timedelta(days=8)
    return max(saturday, today), saturday + timedelta(days=1)


@dataclass(frozen=True)
class ClauseNeeds:
    """一个小句需要的事实类别与删除锚点后的剩余内容。"""

    price: bool
    availability: bool
    catalog: bool
    residue: str

    @property
    def tool_facts(self) -> bool:
        """是否至少要一类由工具回答的事实。"""
        return self.price or self.availability or self.catalog


def _strip(value: str, phrases: Sequence[str]) -> str:
    """按长短语优先删除固定词；英文按完整词边界删除。"""
    for phrase in sorted(phrases, key=len, reverse=True):
        if re.fullmatch(r"[a-z ]+", phrase):
            value = re.sub(rf"\b{re.escape(phrase)}\b", " ", value)
        else:
            value = value.replace(phrase, "")
    return value


def clause_residue(text: str, *, price: bool, availability: bool, catalog: bool) -> str:
    """删除已覆盖事实的操作锚点、小句目标与纯形式词后的剩余内容。

    只删被证据覆盖的那类事实的锚点；某类事实没有证据时，它的锚点留在剩余里，由调用方按
    工具缺口处理（不可转交）。剩余为空表示这个小句没有别的需要回答的内容。
    """
    value = normalize_text(text)
    for match in reversed(list(_ABSOLUTE_DATE.finditer(value))):
        tail = _RANGE_TAIL.match(value, match.end())
        stop = tail.end() if tail is not None else match.end()
        value = value[: match.start()] + " " + value[stop:]
    value = _RELATIVE_DATE.sub(" ", value)
    value = _NIGHTS_PATTERN.sub(" ", value)
    value = _ROOM_NUMBER.sub(" ", value)
    anchors: list[str] = []
    if availability:
        anchors.extend(AVAILABILITY_ANCHORS)
    if price:
        anchors.extend(PRICE_ANCHORS)
    if catalog:
        anchors.extend(CATALOG_ANCHORS)
    value = _strip(value, anchors)
    value = re.sub(r"入住|退房|check.?in|check.?out|\bnights?\b|\bstay\b", " ", value)
    value = _strip(value, _ROOM_NOUNS)
    value = _FORM_WORDS_EN.sub(" ", value)
    value = _strip(value, _FORM_WORDS_ZH)
    return _NOISE.sub("", value)


def clause_needs(text: str) -> ClauseNeeds:
    """按现有正向识别得出小句要的事实，并按「这些事实都已覆盖」算出剩余（调用前用）。

    剩余只依赖原文、锚点与形式词：调用后某类锚点若没被证据覆盖，缺的是工具事实，原因为
    工具未覆盖或日期待澄清，本来就不可转交；可转交的只有这里算出的非锚点剩余。
    """
    price = asks_room_price(text)
    availability = asks_stay_availability(text)
    catalog = asks_property_catalog(text)
    residue = clause_residue(text, price=price, availability=availability, catalog=catalog)
    return ClauseNeeds(price, availability, catalog, residue)


@dataclass(frozen=True)
class ClauseTarget:
    """小句要核对的房源与日期；`unbound` 表示无法证明这个小句问的是哪间房、哪段日期。"""

    property_ids: tuple[int | None, ...]
    check_in: date | None
    check_out: date | None
    unbound: bool


def clause_target(
    clause: str,
    *,
    item_rooms: Sequence[str],
    item_relative_dates: bool,
    item_date_names: int,
    item_check_in: date | None,
    item_check_out: date | None,
    room_properties: dict[str, int | None],
    item_property: int | None,
    year: int,
) -> ClauseTarget:
    """按小句自己点名的房号与日期确定目标，没点名时才继承计划项已核验的目标（V4-R1）。

    继承条件：该项原文只有一个房号（或没有），日期写法只有一种，且没有无法比较的相对日期
    与之并存；否则无法证明继承关系，记为未绑定。映射不到的房号没有证据，同样不算覆盖。
    """
    rooms = clause_rooms(clause)
    dates = clause_dates(clause, year)
    if dates.unbindable:
        return ClauseTarget((), None, None, True)
    if rooms:
        properties = tuple(room_properties.get(room) for room in rooms)
    elif len(set(item_rooms)) > 1:
        return ClauseTarget((), None, None, True)
    else:
        properties = (item_property,) if item_property is not None or item_rooms else ()
    if dates.check_in is not None or dates.check_out is not None:
        return ClauseTarget(properties, dates.check_in, dates.check_out, False)
    if dates.relative:
        if item_date_names > 1:
            return ClauseTarget((), None, None, True)
        return ClauseTarget(properties, item_check_in, item_check_out, False)
    if item_date_names > 1 and item_relative_dates:
        return ClauseTarget((), None, None, True)
    return ClauseTarget(properties, item_check_in, item_check_out, False)


def evidence_covers(
    evidence: Sequence[ReplyEvidence], kind: str, target: ClauseTarget
) -> bool:
    """实际证据行是否覆盖这个小句目标的某类事实（房态 availability、参考价 reference_price）。

    每个点名房源都要有该房源的证据行；有日期时入住日与证据入住日、退房日与证据退房日分别
    相等（房态与参考价证据的起止字段都是查询的入住、退房日）。没有点名房源时，任一行同类
    证据（全店列表）即可。
    """
    if target.unbound:
        return False
    rows = [item for item in evidence if item.source_kind == kind]

    def dates_match(item: ReplyEvidence) -> bool:
        """证据日期与目标日期角色逐一相等；目标没有日期时不比较。"""
        if target.check_in is not None and item.target_date != target.check_in:
            return False
        return target.check_out is None or item.target_end_date == target.check_out

    if not target.property_ids:
        return any(dates_match(item) for item in rows)
    return all(
        property_id is not None
        and any(item.property_id == property_id and dates_match(item) for item in rows)
        for property_id in target.property_ids
    )


def missing_nights(
    nights: Sequence[dict[str, object]], check_in: date | None, check_out: date | None
) -> list[date]:
    """所问区间内没有有效参考价的夜晚（修复 Spec V6-R3）；区间外的夜价不能用来补齐。"""
    if check_in is None or check_out is None or check_out <= check_in:
        return []
    priced = {
        str(night.get("date"))
        for night in nights
        if isinstance(night, dict)
        and night.get("date")
        and isinstance(night.get("price"), (int, float))
    }
    expected = [check_in + timedelta(days=offset) for offset in range((check_out - check_in).days)]
    return [day for day in expected if day.isoformat() not in priced]


# 缺口原因：只有「资料缺失」可以转交管家核实（修复 Spec §2.6(1)）；冲突、期间否决、范围
# 未绑定、日期待澄清、工具未覆盖或失败、预算跳过各有自己的澄清或人工复核路径。
GapReason = Literal[
    "knowledge_missing",
    "conflict",
    "period_veto",
    "scope_unbound",
    "date_clarification",
    "tool_uncovered",
    "tool_failed",
    "budget",
    "needs_review",
]


class GapUnit(BaseModel):
    """一个未被覆盖的缺口单元：编号由计划项编号与原文区间生成，本轮内稳定。

    只在适配器与会话层之间传递，不进入客人可见正文，也不写入模型输出结构。
    `text` 是原文片段（未净化），使用方在落库、展示或交给模型前必须净化。
    """

    unit_id: str
    item_id: int
    text: str
    reason: GapReason
    property_id: int | None = None


def unit_id_for(item_id: int, start: int, end: int) -> str:
    """缺口单元编号：计划项编号加原文区间，调用前后同一切句得到同一编号。"""
    return f"{item_id}:{start}-{end}"


class ReferralCandidate(BaseModel):
    """模型选中、本地确认为「资料缺失」的缺口单元，交会话层判断能否转交管家核实。"""

    unit: GapUnit
    canonical_question: str
    category: str
