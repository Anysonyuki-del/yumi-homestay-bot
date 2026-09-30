"""一句多问时核对主模型整合的回答：实时查询里的关键事实不能被改错、不能被漏掉。

1.60.0 起联网结果交给主模型写成一段（用户选定的方案二）。Codex 审查（2026-09-30）
指出原先「回答里的数字都在结果里出现过」的集合判断有三个漏洞：区间写反（21～15℃）
也能通过；营业时间这类不带单位的事实被漏掉查不出来；发现错数字时整段删除，连带删掉
同段的玩法推荐。这里改为按「关键事实」核对：温度、百分比、价格和营业时间，区间按
先后顺序比较；错的只删那一句，漏的补回那一组的查询原文。

ponytail: 规则只认数字类事实；「阵雨」改说「小雨」这类措辞变化核对不到，由真实模型
回归门禁兜底。反复出现文字类事实被改写时，再考虑复用改写器的语义校验。
"""

import re
from dataclasses import dataclass

# 单位归一：℃、°C、度都按温度；％按 %。
_UNIT = r"(℃|°C|度|%|％|元)"
_NUMBER = r"(\d+(?:\.\d+)?)"
_SEPARATOR = r"\s*[～~\-–—至到]\s*"
_RANGE_PATTERN = re.compile(_NUMBER + r"\s*" + _UNIT + "?" + _SEPARATOR + _NUMBER + r"\s*" + _UNIT)
_SINGLE_PATTERN = re.compile(_NUMBER + r"\s*" + _UNIT)
_TIME_PATTERN = re.compile(r"(?<!\d)(\d{1,2})[:：](\d{2})(?!\d)")
_TIME_RANGE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2})[:：](\d{2})" + _SEPARATOR + r"(\d{1,2})[:：](\d{2})(?!\d)"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def _unit(raw: str) -> str:
    """把单位写法归一。"""
    return {"°C": "℃", "度": "℃", "％": "%"}.get(raw, raw)


def _time(hour: str, minute: str) -> str:
    """把 8:30、08：30 统一成 08:30。"""
    return f"{int(hour):02d}:{minute}"


@dataclass(frozen=True)
class _Facts:
    """一段文字里的关键事实。"""

    ranges: frozenset[tuple[str, str, str]]  # （起，止，单位）；时间区间单位记为 time
    values: frozenset[tuple[str, str]]  # （数值，单位），含区间两端；时间记为 (08:30, time)


def _facts(text: str) -> _Facts:
    """抽取温度、百分比、价格和时间，区间保留先后顺序。"""
    ranges: set[tuple[str, str, str]] = set()
    values: set[tuple[str, str]] = set()
    for low, low_unit, high, high_unit in _RANGE_PATTERN.findall(text):
        unit = _unit(high_unit or low_unit)
        ranges.add((low, high, unit))
        values.update({(low, unit), (high, unit)})
    for number, unit in _SINGLE_PATTERN.findall(text):
        values.add((number, _unit(unit)))
    for h1, m1, h2, m2 in _TIME_RANGE_PATTERN.findall(text):
        ranges.add((_time(h1, m1), _time(h2, m2), "time"))
    for hour, minute in _TIME_PATTERN.findall(text):
        values.add((_time(hour, minute), "time"))
    return _Facts(frozenset(ranges), frozenset(values))


def _wrong_facts(sentence: str, live: _Facts) -> bool:
    """这一句是否写了查询结果里没有的关键事实。

    只核对查询结果里出现过的单位：结果只有天气时，句子里的「6:30 开门」「停车 20 元」
    来自审核知识，不在核对范围内。区间写反、两端数字不在结果里都算错。
    """
    checked_units = {unit for _, unit in live.values}
    facts = _facts(sentence)
    for low, high, unit in facts.ranges:
        if unit in checked_units and (low, high, unit) not in live.ranges and not (
            (low, unit) in live.values
            and (high, unit) in live.values
            and (unit == "time" or float(low) <= float(high))
        ):
            return True
    return any(
        unit in checked_units and (value, unit) not in live.values
        for value, unit in facts.values
    )


def _covered(part: _Facts, reply: _Facts) -> bool:
    """这一组查询的主要事实是否出现在回答里：区间原样出现，或两端都出现即算覆盖。

    没有区间时，只要有一个关键数值出现即算覆盖；湿度、阵风这类次要数字允许省略，
    否则模型正常的精简转述也会被判成遗漏、把原文再贴一遍。
    """
    for low, high, unit in part.ranges:
        if (low, high, unit) not in reply.ranges and not (
            (low, unit) in reply.values and (high, unit) in reply.values
        ):
            return False
    if not part.ranges and part.values:
        return bool(part.values & reply.values)
    return True


@dataclass(frozen=True)
class CheckedReply:
    """核对结果：整理后的回答正文，以及需要补回原文的查询组下标。"""

    text: str
    missing: tuple[int, ...]


def check_integrated_reply(reply: str, live_results: list[str]) -> CheckedReply:
    """删掉写错关键事实的句子，找出被漏掉的查询组。

    `live_results` 是各组成功查询的正文（已去掉时效说明）。没有关键事实可核对的组
    （如「今日闭馆」）无法判断是否遗漏，视为已覆盖。
    """
    live = _Facts(
        frozenset(r for text in live_results for r in _facts(text).ranges),
        frozenset(v for text in live_results for v in _facts(text).values),
    )
    kept_lines = []
    for line in reply.splitlines():
        sentences = [item for item in _SENTENCE_SPLIT.split(line) if item]
        kept = [item for item in sentences if not _wrong_facts(item, live)]
        if kept or not sentences:
            kept_lines.append("".join(kept))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()
    reply_facts = _facts(text)
    missing = tuple(
        index
        for index, result in enumerate(live_results)
        if not _covered(_facts(result), reply_facts)
    )
    return CheckedReply(text=text, missing=missing)
