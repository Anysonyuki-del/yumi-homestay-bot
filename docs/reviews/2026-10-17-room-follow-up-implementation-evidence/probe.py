"""1.70.4 实施审查离线证据；断言反例成立，退出 0 不表示修复通过。

从仓库根目录运行：PYTHONPATH=src:.:tests .venv/bin/python <本文件路径>
仅用合成计划、模型、工具和知识，不调用外部接口或生产数据库。
"""

import asyncio
import json
from pathlib import Path

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import AssistantUnavailableError, GapReferral
from homestay_bot.services.knowledge_service import KnowledgeService
from homestay_bot.tools.reply_regression import _Entry, _MemoryKnowledge, judge
from tests.plan_helpers import PLAN_TODAY, plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _decision,
    _Knowledge,
    _respond,
    _tool_message,
    _Tools,
)
from tests.unit.test_room_identity import ARGS, FU_ENTRIES, FU_ROOMS, _rows


async def answer(text, items, *, tool=False, invalid=False, knowledge=None):
    """通过真实计划校验、布局和助手组合，最底层模型与业务工具使用测试替身。"""
    plan = plan_for(text, *items)
    assert plan.ok
    tools = _Tools({"search_availability": _rows("search_availability", (401, 302, 501))})
    responses = ([_tool_message("search_availability", ARGS)] if tool else [])
    responses.append("invalid" if invalid else _decision())
    assistant, _ = _assistant(responses, knowledge or _Knowledge(FU_ENTRIES, FU_ROOMS), tools)
    return await _respond(assistant, text, plan)


def show(label, decision):
    """只输出合成正文、缺口原因和来源，供报告逐项绑定。"""
    print(json.dumps({"case": label, "reply": decision.reply_text,
                      "gaps": [(g.text, g.reason, g.property_id) for g in decision.gap_units]},
                     ensure_ascii=False))


async def main():
    """覆盖原文相邻、职责分流、工具目标、预算原因和门禁错配五条审查边界。"""
    text = "401早餐几点，停车怎么收费，302呢"
    result = await answer(text, [
        ("static_fact", "401早餐几点", {"target_room": "401"}),
        ("static_fact", "302呢", {"target_room": "302"}),
    ])
    assert "302早餐9:00" in result.reply_text and not result.gap_units
    show("E174-1 plan漏中间小句却跨越借早餐", result)

    text = "明天401还有房吗，302呢"
    result = await answer(text, [
        ("stay_query", "明天401还有房吗", {"target_room": "401", **ARGS}),
        ("static_fact", "302呢", {"target_room": "302"}),
    ], tool=True)
    assert any(g.reason == "scope_unbound" for g in result.gap_units)
    assert "302测试房" not in result.reply_text
    show("E174-2a static标签把B改D", result)

    text = "401早餐几点，302呢，明天501还有房吗"
    result = await answer(text, [("stay_query", text, {"target_room": "401", **ARGS})],
                          tool=True)
    assert "302早餐9:00" not in result.reply_text and "302测试房" in result.reply_text
    show("E174-2b 后向工具枚举抢走静态追问", result)

    text = "明天501还有房吗，401早餐几点，302早餐几点"
    result = await answer(text, [("stay_query", text, {"target_room": "501", **ARGS})],
                          tool=True)
    assert all(f"{pid}测试房" in result.reply_text for pid in (401, 302, 501))
    assert not result.gap_units
    show("E174-3 own静态房间混进房态", result)

    text = "明天401早餐几点，302呢"
    try:
        await answer(text, [("stay_query", text, {"target_room": "401", **ARGS})],
                     tool=True, invalid=True)
    except AssistantUnavailableError:
        print("E174-4 纯静态硬调用被拒后invalid：AssistantUnavailableError（既有行为偏差）")
    else:
        raise AssertionError("预期当前协议不可用路径")

    # 使用真实检索服务遵守每次 3000 字预算，避免无预算替身制造假反例。
    entries = [_Entry(9000, "早餐", "早餐几点开始？", "早餐说明" + "甲" * 11650,
                      "Breakfast", "Details", scope="global")]
    entries += [_Entry(pid, "早餐", "早餐几点开始？", f"{pid}早餐9:00开始。" + "乙" * 2700,
                       "Breakfast", "Details", scope="property", property_id=pid)
                for pid in (401, 302, 501)]
    knowledge = KnowledgeService(_MemoryKnowledge(
        entries, [{"id": pid, "room_number": str(pid), "title": ""}
                  for pid in (401, 302, 501)],
    ))
    text = "401早餐几点，302呢，501呢"
    plan = plan_for(text, ("static_fact", text, {"target_room": "401"}))
    assistant, _ = _assistant([_decision()], knowledge)
    identities = await assistant._room_identities()
    layout = await assistant._fact_layout(plan.plan.valid_items, text, None,
                                         (PLAN_TODAY, PLAN_TODAY), PLAN_TODAY, identities)
    initial = await knowledge.retrieve(Language.ZH, text)
    merged, candidates = await assistant._merge_item_knowledge(
        initial, layout, language=Language.ZH, question_text=text,
    )
    assert [item.source_id for item in merged] == [9000, 401, 302]
    assert candidates[(1, 2)] == []
    result = await _respond(assistant, text, plan)
    assert any(g.property_id == 501 and g.reason == "knowledge_missing"
               for g in result.gap_units)
    gap = next(g for g in result.gap_units if g.property_id == 501)
    selected = result.model_copy(update={"gap_referrals": [GapReferral(
        unit_id=gap.unit_id, canonical_question="501早餐几点", category="早餐",
    )]})
    assert assistant._referral_candidates(selected)[0].unit.property_id == 501
    print("E174-5 真实检索：初始11661字，401/302/501各2719字；501被合并总预算跳过")
    print("E174-5 模拟模型选中501缺口后，真实_referral_candidates接受（未建记录或发卡）")
    show("E174-5 预算筛空被误记缺资料", result)

    fixture = json.loads(Path("tests/fixtures/guest_reply_scenarios.json").read_text())
    scenario = next(item for item in fixture["scenarios"] if item["id"] == "K-房号追问")
    for final in ("7701：早餐9:45开始。7702：早餐8:15开始。", "早餐时间为8:15和9:45。"):
        verdict = judge(scenario, {"route": "knowledge", "final": final})
        assert verdict["ok"]
        print(json.dumps({"case": "E174-6 错配或无房号却过门禁", "reply": final,
                          "judge": verdict}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
