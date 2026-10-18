"""复审已支持省略句尾的同项/拆项一致性；退出0证明六次残留及十次控制。

运行：PYTHONPATH=src:.:tests .venv/bin/python <本文件路径>。
仅合成模型与工具，经真实结构识别、计划核验和助手流程。
"""

import asyncio
import json

from homestay_bot.services.item_coverage import is_room_follow_up
from tests.unit.test_room_identity import ARGS, LATER, _v2_turn


async def main():
    """四种有限句尾、两种规划形状及两个出口；不把结构已识别误称为未知措辞。"""
    good, bad = 0, 0
    for fallback in (False, True):
        for tail in ("302呢", "302也是吗", "302也一样吗", "302也这样吗"):
            assert is_room_follow_up(tail)
            quote = f"10月8日401有房吗，{tail}，10月10日501多少钱"
            for same_item in (False, True):
                items = (
                    [("stay_query", quote, dict(ARGS))] if same_item else
                    [("stay_query", "10月8日401有房吗", dict(ARGS)),
                     ("static_fact", tail, {}),
                     ("stay_query", "10月10日501多少钱", dict(LATER))]
                )
                items.append(("static_fact", "401早餐几点，501呢", {}))
                decision = await _v2_turn(
                    quote + "，401早餐几点，501呢", items,
                    [("search_availability", ARGS), ("search_reference_price", LATER)],
                    fallback=fallback,
                )
                expected_failure = same_item and tail != "302呢"
                assert ("可订：401测试房、302测试房" in decision.reply_text) is (
                    not expected_failure
                )
                assert [(g.text, g.reason) for g in decision.gap_units] == (
                    [(tail, "scope_unbound")] if expected_failure else []
                )
                assert "302测试房：10月10日" not in decision.reply_text
                bad += expected_failure
                good += not expected_failure
                print(json.dumps({"tail": tail, "same_item": same_item,
                                  "fallback": fallback, "missing_child": expected_failure,
                                  "reply": decision.reply_text}, ensure_ascii=False))
    assert (bad, good) == (6, 10)


if __name__ == "__main__":
    asyncio.run(main())
