"""1.70.5 工具目标复审，合成输入经过真实助手，不连接外部或生产。

PYTHONPATH=src:.:tests .venv/bin/python <本文件路径> [旧模块的Git引用]
默认断言当前缺陷；带引用时仅把助手模块换成该引用作对照，不改工作区源码。
"""

import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import tests.unit.test_reply_fix2_items as helpers
from tests.plan_helpers import plan_for
from tests.unit.test_room_identity import ARGS, FU_ENTRIES, FU_ROOMS, _rows


class DatedTools:
    """按实际调用参数返回全店三房，日期与每晚价格均来自该次调用。"""

    async def execute(self, name, arguments):
        """房态和价格工具都可返回全店，由助手按需求筛选。"""
        rows = _rows(name, (401, 302, 501))
        for row in rows:
            row.update(arguments)
            if name == "search_reference_price":
                start = date.fromisoformat(arguments["check_in_date"])
                end = date.fromisoformat(arguments["check_out_date"])
                row["nightly_reference_prices"] = [
                    {"date": (start + timedelta(days=i)).isoformat(), "price": 200}
                    for i in range((end - start).days)
                ]
        return rows


async def answer(text, items, calls, fallback):
    """核验计划后执行正常或协议回退链，禁止用未核验计划制造反例。"""
    plan = plan_for(text, *items)
    assert plan.ok
    assistant, _ = helpers._assistant(
        [*(helpers._tool_message(name, args) for name, args in calls),
         "invalid" if fallback else helpers._decision()],
        helpers._Knowledge(FU_ENTRIES, FU_ROOMS), DatedTools(),
    )
    return await helpers._respond(assistant, text, plan)


async def main(legacy):
    """六组输入各验两个出口；对照与当前分别断言已知差异，不把旧版错答当正确目标。"""
    later = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-11"}
    friday = {"check_in_date": "2026-10-09", "check_out_date": "2026-10-11"}
    saturday = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-12"}
    for fallback in (False, True):
        for tool, ask in [("search_availability", "有房吗"),
                          ("search_reference_price", "多少钱")]:
            quote = f"10月8日401{ask}，10月10日302{ask}"
            text = quote + "，501早餐几点，401呢"
            d = await answer(text, [
                ("stay_query", quote, dict(ARGS)),
                ("static_fact", "501早餐几点，401呢", {"target_room": "501"}),
            ], [(tool, ARGS), (tool, later)], fallback)
            assert bool(d.gap_units) is (not legacy)
            assert ("10月10日入住" in d.reply_text if tool == "search_availability"
                    else "302测试房：10月10日" in d.reply_text) is legacy
            emit("COARSE_DATE", fallback, d, tool)

        text = "10月8日401有房吗，501早餐几点，302呢"
        d = await answer(text, [
            ("stay_query", "10月8日401有房吗", {"target_room": "401", **later}),
            ("static_fact", "501早餐几点，302呢", {"target_room": "501"}),
        ], [("search_availability", ARGS)], fallback)
        assert ("401测试房" in d.reply_text) is legacy
        emit("PLAN_DATE_DIFFERS_FROM_QUOTE", fallback, d)

        text = "明天401有房吗，能订吗，501早餐几点，302呢"
        d = await answer(text, [
            ("stay_query", "明天401有房吗，能订吗", {"target_room": "401", **ARGS}),
            ("static_fact", "501早餐几点，302呢", {"target_room": "501"}),
        ], [("search_availability", ARGS)], fallback)
        assert ("302测试房" in d.reply_text) is (not legacy)
        assert not d.gap_units
        emit("INHERITED_ROOM", fallback, d)

        text = "这周末401有房吗，501早餐几点，302呢"
        for call in (friday, saturday):
            d = await answer(text, [
                ("stay_query", "这周末401有房吗", {"target_room": "401", **friday}),
                ("static_fact", "501早餐几点，302呢", {"target_room": "501"}),
            ], [("search_availability", call)], fallback)
            shown = legacy or call == friday
            assert ("方案二" in d.reply_text) is shown
            assert bool(d.gap_units) is (not shown)
            emit("WEEKEND_CALL_" + call["check_in_date"], fallback, d)


def emit(label, fallback, decision, tool="search_availability"):
    """只输出合成客人正文与缺口，不包含真实配置、身份或外部数据。"""
    print(json.dumps({"case": label, "tool": tool, "fallback": fallback,
                      "reply": decision.reply_text,
                      "gaps": [(g.text, g.reason) for g in decision.gap_units]},
                     ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) == 1:
        asyncio.run(main(False))
    else:
        # 在临时模块载入旧代码；只替换测试工厂引用，进程退出即消失，不覆盖用户改动。
        source = subprocess.check_output([
            "git", "show", f"{sys.argv[1]}:src/homestay_bot/integrations/deepseek_client.py"
        ])
        with tempfile.TemporaryDirectory(prefix="r175-control-") as directory:
            path = Path(directory) / "old_assistant.py"
            path.write_bytes(source)
            spec = importlib.util.spec_from_file_location("r175_old_assistant", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            helpers.DeepSeekGuestAssistant = module.DeepSeekGuestAssistant
            asyncio.run(main(True))
