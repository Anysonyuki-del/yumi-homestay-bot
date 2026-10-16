"""离线复现房号身份实施审查反例；退出 0 证明反例存在，不表示修复通过。

模型和工具响应均为合成数据；身份、覆盖判定及最终回复经过真实助手代码。
"""

import asyncio
import json

from homestay_bot.integrations.deepseek_client import AssistantRequestContext
from homestay_bot.services.item_coverage import RoomIdentity, resolve_rooms, room_mentions
from homestay_bot.services.knowledge_service import KnowledgeService
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _decision,
    _Knowledge,
    _respond,
    _snippet,
    _tool_message,
    _Tools,
)

ARGS = {"check_in_date": "2026-10-08", "check_out_date": "2026-10-09"}
ROOMS = {"302": 302, "1栋1803": 1803, "2栋1803": 2803}
ROWS = [
    {"property_id": property_id, "property_title": title, "stay_available": True, **ARGS}
    for property_id, title in [(302, "302家庭房"), (1803, "1栋1803"), (2803, "2栋1803")]
]


def show(label, decision):
    """只输出合成正文、资料来源与缺口，便于核对最终消费结果。"""
    result = {
        "case": label,
        "reply": decision.reply_text,
        "evidence": [
            (e.source_kind, e.source_id, e.property_id)
            for part in decision.reply_parts for e in part.evidence
        ],
        "gaps": [(gap.reason, gap.property_id) for gap in decision.gap_units],
    }
    print(json.dumps(result, ensure_ascii=False))


async def availability(text, knowledge, rows, *, fallback=False):
    """运行合成工具调用后的正常或协议回退回复，覆盖实际输出与覆盖判定。"""
    assistant, _ = _assistant(
        [_tool_message("search_availability", ARGS), "invalid" if fallback else _decision()],
        knowledge,
        _Tools({"search_availability": rows}),
    )
    return await _respond(assistant, text, plan_for(text, ("stay_query", text, {})))


class BrokenProfiles:
    """仅房源身份读取失败，知识读取正常，避免把其他故障混入反例。"""

    async def list_room_profiles(self):
        """注入一次可重复的房源读取故障。"""
        raise RuntimeError("synthetic_read_failure")

    async def list_active(self):
        """无启用知识；不影响本探针的合成房态工具。"""
        return []


async def main():
    """验证四项实施问题；所有断言检查当前缺陷，修复后应改为正确行为的回归。"""
    text = "2栋1803有浴缸吗"
    entries = [
        _snippet(731, "房间有浴缸吗？", "1栋1803没有浴缸，只有淋浴。", category="卫浴",
                 scope="property", property_id=101),
        _snippet(732, "房间有浴缸吗？", "2栋1803有独立浴缸。", category="卫浴",
                 scope="property", property_id=202),
    ]
    assistant, _ = _assistant(
        [_decision()], _Knowledge(entries, {"1803": 101, "2栋1803": 202})
    )
    decision = await _respond(
        assistant, text, plan_for(text, ("static_fact", text, {})),
        request_context=AssistantRequestContext(property_id=101),
    )
    assert "只有淋浴" in decision.reply_text and "独立浴缸" not in decision.reply_text
    assert {e.property_id for p in decision.reply_parts for e in p.evidence} == {101}
    show("IRI-R1-building-wildcard", decision)

    for fallback in (False, True):
        decision = await availability(
            "明天302和1803还有房吗", _Knowledge([], ROOMS), ROWS, fallback=fallback
        )
        ids = {e.property_id for p in decision.reply_parts for e in p.evidence}
        assert ids == {1803, 2803} and 302 not in ids
        show(f"IRI-R2-mixed-fallback-{fallback}", decision)
    decision = await availability("明天302和9999还有房吗", _Knowledge([], ROOMS), ROWS)
    assert "没有找到" not in decision.reply_text
    show("IRI-R2-unknown-with-known", decision)

    identities = [RoomIdentity(i, b, n) for i, b, n in [
        (302, None, "302"), (1803, "1", "1803"), (2803, "2", "1803"),
    ]]
    first = resolve_rooms(room_mentions("9999和1803"), identities, None)
    second = resolve_rooms(room_mentions("1803和9999"), identities, None)
    assert (first.status, first.property_ids) == ("unknown", ())
    assert (second.status, second.property_ids) == ("ambiguous", (1803, 2803))
    print("IRI-R2-order-dependent", first, second)

    decision = await availability("明天302还有房吗", _Knowledge([], ROOMS), ROWS[1:])
    assert {e.property_id for p in decision.reply_parts for e in p.evidence} == {1803, 2803}
    assert "没有找到" not in decision.reply_text
    show("IRI-R3-known-room-not-in-result", decision)

    decision = await availability("明天302还有房吗", KnowledgeService(BrokenProfiles()), ROWS)
    assert "没有找到「302」这个房间" in decision.reply_text
    assert "可订：302家庭房" in decision.reply_text
    show("IRI-R4-profile-read-failure", decision)


if __name__ == "__main__":
    asyncio.run(main())
