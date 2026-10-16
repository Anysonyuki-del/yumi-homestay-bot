"""复审枚举交接后的日期与单房号片段；退出0证明文中反例仍在，不表示修复通过。"""

import asyncio
import json

from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _decision,
    _Knowledge,
    _respond,
    _tool_message,
    _Tools,
)

ROOMS = {"302": 302, "401": 401}


async def answer(text, tool, day, property_ids, fallback):
    """冻结今日为测试端口的10月7日，查询参数与计划日期相同，仅原文片段日期不同。"""
    args = {"check_in_date": f"2026-10-{day:02}", "check_out_date": f"2026-10-{day + 1:02}"}
    rows = [
        {"property_id": value, "property_title": f"{value}测试房", "stay_available": True,
         "nightly_reference_prices": [{"date": args["check_in_date"], "price": 200}], **args}
        for value in property_ids
    ]
    assistant, _ = _assistant(
        [_tool_message(tool, args), "invalid" if fallback else _decision()],
        _Knowledge([], ROOMS), _Tools({tool: rows}),
    )
    return await _respond(assistant, text, plan_for(text, ("stay_query", text, args)))


def show(label, tool, fallback, decision):
    """只输出合成正文、证据日期及缺口，不接触真实客人数据。"""
    print(json.dumps({
        "case": label, "tool": tool, "fallback": fallback, "reply": decision.reply_text,
        "knowledge_gap": decision.knowledge_gap,
        "evidence": [(e.property_id, str(e.target_date))
                     for part in decision.reply_parts for e in part.evidence],
        "gaps": [(gap.reason, gap.text) for gap in decision.gap_units],
    }, ensure_ascii=False))


async def main():
    """覆盖房态参考价、正常回退；对照带绝对日期保守处理与已交接片段仍被静态取证。"""
    for fallback in (False, True):
        for tool, ending in (("search_availability", "还有房吗"),
                             ("search_reference_price", "多少钱")):
            for prefix in ("明天", "第二天"):
                text = f"{prefix}302，10月10日401{ending}"
                decision = await answer(text, tool, 10, (302, 401), fallback)
                assert not decision.knowledge_gap and not decision.gap_units
                evidence = [e for part in decision.reply_parts for e in part.evidence]
                assert {e.property_id for e in evidence} == {302, 401}
                assert all(str(e.target_date) == "2026-10-10" for e in evidence)
                show("IRI-F7-relative-date-lost", tool, fallback, decision)
            control = await answer(f"10月8日302，10月10日401{ending}",
                                   tool, 10, (302, 401), fallback)
            assert control.knowledge_gap
            assert any(gap.text == "10月8日302" for gap in control.gap_units)
            control = await answer(f"明天302，10月8日401{ending}", tool, 8,
                                   (302, 401), fallback)
            assert not control.gap_units
            partial = await answer(f"明天302，401{ending}", tool, 8, (401,), fallback)
            assert partial.knowledge_gap and "302" not in partial.reply_text
            assert [(gap.reason, gap.text) for gap in partial.gap_units] == [
                ("tool_uncovered", f"401{ending}")
            ]
            show("IRI-F6-uncovered-label-points-to-returned-room", tool, fallback, partial)
        for text in ("明天302，有房吗", "明天302，302还有房吗"):
            decision = await answer(text, "search_availability", 8, (302,), fallback)
            assert [(gap.reason, gap.text) for gap in decision.gap_units] == [
                ("knowledge_missing", "明天302")
            ]
            show("IRI-C3-delegated-fragment-still-static", "search_availability",
                 fallback, decision)


if __name__ == "__main__":
    asyncio.run(main())
