"""1.70.5 v2 复审：B 追问继承的日期/方案及单退房端点。

PYTHONPATH=src:.:tests .venv/bin/python <本文件路径>
退出0表示当前反例和成对控制成立，不表示已修复；仅合成模型与工具。
"""

import asyncio
import json

from tests.unit.test_room_identity import ARGS, FRIDAY, LATER, SATURDAY, _v2_turn


def show(label, fallback, decision):
    """只输出合成正文及缺口，便于人工复核房源与日期对应关系。"""
    print(json.dumps({"case": label, "fallback": fallback, "reply": decision.reply_text,
                      "gaps": [(g.text, g.reason) for g in decision.gap_units]},
                     ensure_ascii=False))


async def main():
    """五种输入各覆盖正常及回退；明确父日期与合法周末方案分别验证。"""
    for fallback in (False, True):
        for label, parent, planned, called in [
            ("B_WEEKEND", "这周末401有房吗", FRIDAY, SATURDAY),
            ("B_QUOTE", "10月8日401有房吗", LATER, ARGS),
            ("B_CONTROL", "10月8日401有房吗", ARGS, ARGS),
        ]:
            text = parent + "，302呢，501早餐几点，401呢"
            decision = await _v2_turn(text, [
                ("stay_query", parent, {"target_room": "401", **planned}),
                ("static_fact", "302呢", {}),
                ("static_fact", "501早餐几点，401呢", {"target_room": "501"}),
            ], [("search_availability", called)], fallback=fallback)
            if label == "B_WEEKEND":
                first, second = decision.reply_text.split("方案二")
                assert "302测试房" in first and "302测试房" not in second
                assert not decision.gap_units
            elif label == "B_QUOTE":
                assert "401测试房" in decision.reply_text
                assert "302测试房" not in decision.reply_text
                assert [(g.text, g.reason) for g in decision.gap_units] == [
                    ("302呢", "tool_uncovered")
                ]
            else:
                assert "401测试房、302测试房" in decision.reply_text
                assert not decision.gap_units
            show(label, fallback, decision)

        text = "401在10月11日退房有房吗，501早餐几点，302呢"
        for called in [ARGS, {"check_in_date": "2026-10-08",
                              "check_out_date": "2026-10-11"}]:
            decision = await _v2_turn(text, [
                ("stay_query", "401在10月11日退房有房吗", {"target_room": "401", **called}),
                ("static_fact", "501早餐几点，302呢", {"target_room": "501"}),
            ], [("search_availability", called)], fallback=fallback)
            assert "可订：401测试房" in decision.reply_text
            wrong_end = called["check_out_date"] == "2026-10-09"
            assert any(g.reason == "tool_uncovered" for g in decision.gap_units) is wrong_end
            show("CHECKOUT_WRONG" if wrong_end else "CHECKOUT_CONTROL", fallback, decision)


if __name__ == "__main__":
    asyncio.run(main())
