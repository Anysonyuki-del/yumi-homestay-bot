"""复审 IRI 修复的组合边界；退出 0 表示反例仍在，不表示修复通过。

身份、日期和最终回复走真实助手；只复用合成模型及工具测试端口，不访问外部服务。
"""

import asyncio
import json
from itertools import permutations

from homestay_bot.services.item_coverage import (
    RoomIdentity,
    clause_dates,
    clause_needs,
    resolve_rooms,
    room_mentions,
)
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _decision,
    _Knowledge,
    _respond,
    _tool_message,
    _Tools,
)

ARGS = {"check_in_date": "2026-10-08", "check_out_date": "2026-10-09"}
ROOMS = {"302": 302, "401": 401, "9999": 9999}
ROWS = [
    {"property_id": value, "property_title": f"{value}测试房", "stay_available": True, **ARGS}
    for value in ROOMS.values()
]


async def answer(text, fallback):
    """同一资料分别走合法 JSON 和协议失败后的房态回退出口。"""
    assistant, _ = _assistant(
        [_tool_message("search_availability", ARGS), "invalid" if fallback else _decision()],
        _Knowledge([], ROOMS),
        _Tools({"search_availability": ROWS}),
    )
    return await _respond(assistant, text, plan_for(text, ("stay_query", text, {})))


def ids(decision):
    """核对实际最终证据，不能只从正文的数字推断选中了哪些房源。"""
    return {e.property_id for part in decision.reply_parts for e in part.evidence}


def show(label, fallback, decision):
    """仅输出本探针的合成内容与证据编号，供报告复核。"""
    print(json.dumps({
        "case": label, "fallback": fallback, "reply": decision.reply_text,
        "properties": sorted(ids(decision)),
        "gaps": [(gap.reason, gap.text) for gap in decision.gap_units],
    }, ensure_ascii=False))


async def main():
    """先验证已修复的聚合与连接词控制，再固定日期和切句抢先消费的反例。"""
    identities = [
        RoomIdentity(302, None, "302"), RoomIdentity(1803, "1", "1803"),
        RoomIdentity(2803, "2", "1803"),
    ]
    for sequence in permutations(["302", "1803", "9999", "8888"]):
        resolution = resolve_rooms(room_mentions("和".join(sequence)), identities, None)
        assert resolution.status == "ambiguous"
        assert set(resolution.property_ids) == {302, 1803, 2803}
        assert set(resolution.unresolved) == {"9999", "8888"}
    print("mixed_resolution_permutations: 24 passed")
    for connector in ["和", "与", "跟", "及", "或", "或者", "还有", "、", ",", "，"]:
        text = f"明天302{connector}401还有房吗"
        assert clause_needs(text).residue == ""
        assert clause_needs(text + "，入口在哪").residue == "入口在哪"
    print("residue_controls: 20 passed; independent entrance question retained")

    slash = "明天302/401还有房吗"
    assert room_mentions(slash) == ()
    assert clause_dates(slash, 2026).unbindable
    assert clause_needs(slash).residue == "31"
    assert [mention.number for mention in room_mentions("10/08入住302")] == ["302"]
    for fallback in (False, True):
        for connector in ("和", "、"):
            control = await answer(f"明天302{connector}401还有房吗", fallback)
            assert ids(control) == {302, 401} and not control.gap_units
        decision = await answer(slash, fallback)
        assert ids(decision) == {302, 401, 9999}
        assert "尚未确认民宿专属信息" in decision.reply_text
        show("IRI-F5-slash-date-collision", fallback, decision)
        for connector in (",", "，"):
            decision = await answer(f"明天302{connector}401还有房吗", fallback)
            assert ids(decision) == {302, 401}
            assert [(gap.reason, gap.text) for gap in decision.gap_units] == [
                ("scope_unbound", "明天302")
            ]
            show("IRI-C1-comma-enumeration", fallback, decision)


if __name__ == "__main__":
    asyncio.run(main())
