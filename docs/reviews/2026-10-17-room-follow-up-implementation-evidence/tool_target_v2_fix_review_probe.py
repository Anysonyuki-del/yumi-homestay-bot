"""离线复审同项追问的工具类型绑定；退出 0 证明残留反例和拆项对照成立。

运行：PYTHONPATH=src:.:tests .venv/bin/python <本文件路径>。
模型、房源与工具均为合成数据，不访问真实服务。
"""

import asyncio
import json

from tests.unit.test_room_identity import ARGS, LATER, _v2_turn


async def main():
    """双向混合工具问题分别按同项和拆项规划，比较正常及协议回退出口。"""
    for fallback in (False, True):
        for first_ask, second_ask, first_tool, second_tool, extra in [
            ("有房吗", "多少钱", "search_availability", "search_reference_price", "房态"),
            ("多少钱", "有房吗", "search_reference_price", "search_availability", "参考价"),
        ]:
            first, second = f"10月8日401{first_ask}", f"10月10日501{second_ask}"
            parent = f"{first}，{second}，302呢"
            for same_item in (False, True):
                items = (
                    [("stay_query", parent, dict(ARGS))] if same_item else
                    [("stay_query", first, dict(ARGS)),
                     ("stay_query", second, dict(LATER)), ("static_fact", "302呢", {})]
                )
                items.append(("static_fact", "401早餐几点，501呢", {}))
                decision = await _v2_turn(
                    parent + "，401早餐几点，501呢", items,
                    [(first_tool, ARGS), (second_tool, LATER)], fallback=fallback,
                )
                gaps = [(g.text, g.reason) for g in decision.gap_units]
                assert gaps == ([("302呢", "tool_uncovered")] if same_item else [])
                assert (f"没有查到对应的{extra}" in decision.reply_text) is same_item
                assert "302测试房" in decision.reply_text
                print(json.dumps({"same_item": same_item, "fallback": fallback,
                                  "parent_fact": second_tool, "reply": decision.reply_text,
                                  "gaps": gaps}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
