"""离线复现：无有效父句的省略追问被枚举接到后句；退出0证明反例与拆项对照。

运行：PYTHONPATH=src:.:tests .venv/bin/python <本文件路径>。
仅使用合成模型、知识与工具，经过真实计划校验和助手流程。
"""

import asyncio
import json

from tests.unit.test_room_identity import ARGS, LATER, _v2_turn


async def main():
    """换行、无父句两种边界，按同项/拆项及正常/协议回退做八次对照。"""
    for fallback in (False, True):
        for prefix in ("10月8日401有房吗\n", ""):
            quote = prefix + "302呢，10月10日501有房吗"
            for same_item in (False, True):
                if same_item:
                    items = [("stay_query", quote, dict(ARGS if prefix else LATER))]
                else:
                    items = ([("stay_query", prefix.strip(), dict(ARGS))] if prefix else [])
                    items += [("static_fact", "302呢", {}),
                              ("stay_query", "10月10日501有房吗", dict(LATER))]
                items.append(("static_fact", "401早餐几点，501呢", {}))
                calls = ([("search_availability", ARGS)] if prefix else [])
                calls.append(("search_availability", LATER))
                decision = await _v2_turn(quote + "，401早餐几点，501呢", items, calls,
                                          fallback=fallback)
                assert "7:30" in decision.reply_text
                assert ("可订：302测试房、501测试房" in decision.reply_text) is same_item
                assert [(g.text, g.reason) for g in decision.gap_units] == (
                    [] if same_item else [("302呢", "scope_unbound")]
                )
                print(json.dumps({"case": "newline" if prefix else "no_parent",
                                  "same_item": same_item, "fallback": fallback,
                                  "reply": decision.reply_text,
                                  "gaps": [(g.text, g.reason) for g in decision.gap_units]},
                                 ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
