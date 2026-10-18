"""离线复审：后续工具问题抢走前句的房号追问；退出0表示反例及拆项对照成立。

运行：PYTHONPATH=src:.:tests .venv/bin/python <本文件路径>。
仅使用合成模型与工具；经过真实计划核验和助手流程，不发送外部消息。
"""

import asyncio
import json

from tests.unit.test_room_identity import ARGS, LATER, _v2_turn


async def main():
    """两种工具顺序、同项与拆项、正常及回退；固定原文只改变规划形状。"""
    for fallback in (False, True):
        for first_ask, second_ask, first_tool, second_tool in [
            ("有房吗", "多少钱", "search_availability", "search_reference_price"),
            ("多少钱", "有房吗", "search_reference_price", "search_availability"),
        ]:
            first, second = f"10月8日401{first_ask}", f"10月10日501{second_ask}"
            quote = f"{first}，302呢，{second}"
            for same_item in (False, True):
                items = (
                    [("stay_query", quote, dict(ARGS))] if same_item else
                    [("stay_query", first, dict(ARGS)), ("static_fact", "302呢", {}),
                     ("stay_query", second, dict(LATER))]
                )
                items.append(("static_fact", "401早餐几点，501呢", {}))
                decision = await _v2_turn(
                    quote + "，401早餐几点，501呢", items,
                    [(first_tool, ARGS), (second_tool, LATER)], fallback=fallback,
                )
                reply = decision.reply_text
                assert not decision.gap_units
                if first_tool == "search_reference_price":
                    assert ("302测试房：10月8日" in reply) is (not same_item)
                else:
                    assert ("可订：401测试房、302测试房" in reply) is (not same_item)
                if second_tool == "search_reference_price":
                    assert ("302测试房：10月10日" in reply) is same_item
                else:
                    assert ("可订：302测试房、501测试房" in reply) is same_item
                print(json.dumps({"same_item": same_item, "fallback": fallback,
                                  "parent_fact": first_tool, "reply": reply},
                                 ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
