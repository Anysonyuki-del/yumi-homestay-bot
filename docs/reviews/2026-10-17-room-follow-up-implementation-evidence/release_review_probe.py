"""1.70.4 发布复审：跨日期目标串用、全店目标丢失及空小句检索控制。

从根目录运行：PYTHONPATH=src:.:tests .venv/bin/python <本文件路径>
退出 0 表示两个缺陷仍成立、边界修复控制通过；全部输入合成，不连接外部或生产。
"""

import asyncio

from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _decision,
    _Knowledge,
    _respond,
    _snippet,
    _tool_message,
)
from tests.unit.test_room_identity import ARGS, FU_ENTRIES, FU_ROOMS, _rows


class DatedTools:
    """每次调用返回三房，逐行日期与本次参数一致，避免错日替身制造假反例。"""

    async def execute(self, name, arguments):
        """同步住宿区间和逐夜参考价；未点名过滤是上游工具的正常返回形状。"""
        rows = _rows(name, (401, 302, 501))
        for row in rows:
            row.update(arguments)
            if name == "search_reference_price":
                row["nightly_reference_prices"] = [
                    {"date": arguments["check_in_date"], "price": 200}
                ]
        return rows


async def main():
    """经真实助手核验合法计划，覆盖正常与工具后协议回退，断言最终正文及缺口。"""
    later = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-11"}
    for name, ask in [("search_availability", "有房吗"),
                      ("search_reference_price", "多少钱")]:
        for fallback in (False, True):
            text = f"10月8日401{ask}，10月10日302{ask}，501早餐几点，401呢"
            plan = plan_for(
                text,
                ("stay_query", f"10月8日401{ask}", {"target_room": "401", **ARGS}),
                ("stay_query", f"10月10日302{ask}", {"target_room": "302", **later}),
                ("static_fact", "501早餐几点，401呢", {"target_room": "501"}),
            )
            assert plan.ok
            assistant, _ = _assistant(
                [_tool_message(name, ARGS), _tool_message(name, later),
                 "invalid" if fallback else _decision()],
                _Knowledge(FU_ENTRIES, FU_ROOMS), DatedTools(),
            )
            result = await _respond(assistant, text, plan)
            assert not result.gap_units
            if name == "search_availability":
                assert result.reply_text.count("可订：401测试房、302测试房") == 2
            else:
                assert "302测试房：10月8日 200元" in result.reply_text
                assert "401测试房：10月10日 200元" in result.reply_text
            print("DATE_TARGET", name, fallback, result.reply_text, sep="\n")

    for fallback in (False, True):
        text = "明天还有房吗，明天302有房吗，501早餐几点，401呢"
        plan = plan_for(
            text,
            ("stay_query", "明天还有房吗", dict(ARGS)),
            ("stay_query", "明天302有房吗", {"target_room": "302", **ARGS}),
            ("static_fact", "501早餐几点，401呢", {"target_room": "501"}),
        )
        assert plan.ok
        assistant, _ = _assistant(
            [_tool_message("search_availability", ARGS),
             "invalid" if fallback else _decision()],
            _Knowledge(FU_ENTRIES, FU_ROOMS), DatedTools(),
        )
        result = await _respond(assistant, text, plan)
        assert "可订：302测试房" in result.reply_text
        assert "401测试房" not in result.reply_text and "501测试房" not in result.reply_text
        assert not result.gap_units
        print("GLOBAL_TARGET", fallback, result.reply_text, sep="\n")

    # 本地核验目前允许只摘录问号的计划，布局为空；检索守卫必须显式要求存在单元。
    text = "早餐几点？"
    plan = plan_for(text, ("static_fact", "？", {"question": "早餐几点"}))
    assert plan.ok
    knowledge = _Knowledge([_snippet(9876, "早餐几点", "早餐8:00开始", category="早餐")])
    assistant, _ = _assistant([_decision()], knowledge)
    result = await _respond(assistant, text, plan)
    assert any(query == "早餐几点" for query, _ in knowledge.calls)
    assert "早餐8:00开始" in result.reply_text
    print("EMPTY_CLAUSES_CONTROL", "项级检索执行、审核答案保留")


if __name__ == "__main__":
    asyncio.run(main())
