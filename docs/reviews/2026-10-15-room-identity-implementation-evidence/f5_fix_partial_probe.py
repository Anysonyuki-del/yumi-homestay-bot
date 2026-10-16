"""复审 F5 修订：完整结果的控制通过，逗号枚举漏行反例成立时退出 0。

模型与工具是合成端口，规划核验、取证、正常和回退组合走真实助手；不访问外部服务。
"""

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

ARGS = {"check_in_date": "2026-10-08", "check_out_date": "2026-10-09"}
ROOMS = {"302": 302, "401": 401, "9999": 9999}
ROWS = [
    {"property_id": value, "property_title": f"{value}测试房", "stay_available": True,
     "nightly_reference_prices": [{"date": "2026-10-08", "price": 200}], **ARGS}
    for value in ROOMS.values()
]


async def answer(text, name, rows, fallback):
    """只改变问题连接符或工具行，其他输入固定，比较最终覆盖结果。"""
    assistant, _ = _assistant(
        [_tool_message(name, ARGS), "invalid" if fallback else _decision()],
        _Knowledge([], ROOMS), _Tools({name: rows}),
    )
    return await _respond(assistant, text, plan_for(text, ("stay_query", text, {})))


def ids(decision):
    """读取最终证据的真实房源编号，防止只比较文案掩盖目标丢失。"""
    return {e.property_id for part in decision.reply_parts for e in part.evidence}


async def main():
    """确认斜杠与完整枚举闭合，再复现房态和参考价的部分结果被当成全部回答。"""
    for fallback in (False, True):
        for name, ending in (
            ("search_availability", "还有房吗"),
            ("search_reference_price", "多少钱"),
        ):
            for connector in ("和", "/", "、", ",", "，"):
                text = f"明天302{connector}401{ending}"
                full = await answer(text, name, ROWS, fallback)
                assert ids(full) == {302, 401} and not full.gap_units
                partial = await answer(text, name, ROWS[1:2], fallback)
                assert ids(partial) == {401}
                if connector in (",", "，"):
                    assert not partial.knowledge_gap and not partial.gap_units
                    assert "302" not in partial.reply_text
                else:
                    assert partial.knowledge_gap
                    assert any(gap.reason == "tool_uncovered" for gap in partial.gap_units)
                print(json.dumps({
                    "connector": connector, "tool": name, "fallback": fallback,
                    "partial_properties": sorted(ids(partial)),
                    "knowledge_gap": partial.knowledge_gap,
                    "gaps": [(gap.reason, gap.text) for gap in partial.gap_units],
                    "reply": partial.reply_text,
                }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
