"""R174 修复复审：政策咨询被拦、不同工具目标串用的离线证据。

从仓库根目录运行：PYTHONPATH=src:.:tests .venv/bin/python <本文件路径>
退出0表示反例成立；只用合成模型、工具与资料，不连接外部或生产。
"""

import asyncio

from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.services.answer_policy import resolve_task_request
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
from tests.unit.test_room_identity import ARGS, FU_ENTRIES, FU_ROOMS, _rows


async def policy_probe():
    """同一计划及审核资料，仅关闭新增动作守卫作对照，定位少答来自守卫而非缺资料。"""
    for question, answer, category in [
        ("可以加床吗", "可以加床，费用100元。", "加床"),
        ("能延迟退房吗", "可以延迟退房，需提前确认。", "退房"),
    ]:
        text = f"401{question}，302呢"
        entries = [_snippet(pid, question, f"{pid}{answer}", category=category,
                            scope="property", property_id=pid) for pid in (401, 302)]
        plan = plan_for(text, ("static_fact", text, {"target_room": "401"}))
        assert plan.ok and not resolve_task_request(plan, text).register
        assistant, _ = _assistant([_decision()], _Knowledge(entries, FU_ROOMS))
        result = await _respond(assistant, text, plan)
        assert [g.reason for g in result.gap_units] == ["scope_unbound"]
        assert "302" + answer not in result.reply_text
        print("POLICY", text, result.reply_text, sep="\n")
        original = DeepSeekGuestAssistant._requests_action
        DeepSeekGuestAssistant._requests_action = staticmethod(lambda text: False)
        try:
            assistant, _ = _assistant([_decision()], _Knowledge(entries, FU_ROOMS))
            control = await _respond(assistant, text, plan)
            assert "302" + answer in control.reply_text and not control.gap_units
            print("CONTROL: 仅关闭新守卫", control.reply_text, sep="\n")
        finally:
            DeepSeekGuestAssistant._requests_action = staticmethod(original)


async def mixed_tools_probe():
    """401只问房态、302只问参考价，但两个工具共用整轮目标，最终展示了交叉房源。"""
    text = "明天401有房吗，明天302多少钱，501早餐几点，401呢"
    plan = plan_for(
        text,
        ("stay_query", "明天401有房吗", {"target_room": "401", **ARGS}),
        ("stay_query", "明天302多少钱", {"target_room": "302", **ARGS}),
        ("static_fact", "501早餐几点，401呢", {"target_room": "501"}),
    )
    assert plan.ok
    tools = _Tools({name: _rows(name, (401, 302, 501))
                    for name in ("search_availability", "search_reference_price")})
    assistant, _ = _assistant([
        _tool_message("search_availability", ARGS),
        _tool_message("search_reference_price", ARGS),
        _decision(),
    ], _Knowledge(FU_ENTRIES, FU_ROOMS), tools)
    result = await _respond(assistant, text, plan)
    assert "可订：401测试房、302测试房" in result.reply_text
    assert "401测试房：10月8日 200元" in result.reply_text
    assert not result.gap_units
    print("MIXED", text, result.reply_text, sep="\n")


async def main():
    """顺序执行两个独立控制，确保进程内临时替换已经恢复再检验工具链。"""
    await policy_probe()
    await mixed_tools_probe()


if __name__ == "__main__":
    asyncio.run(main())
